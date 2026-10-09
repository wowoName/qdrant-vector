"""
对话服务 - RAG (Retrieval-Augmented Generation)

基于向量检索 + LLM 实现的知识库问答服务。

架构升级：
- 存储：DB 持久化（冷存储） + Redis 热缓存（最近对话）
- 上下文：TokenBudgetComposedStrategy（工具裁剪→摘要→滑动窗口→截断）
- 压缩：增量滚动摘要，三层（far/mid/near）分层摘要
- 并发：乐观锁版本号控制
- 灵活模式：支持纯大模型对话（无知识库）和知识库 RAG 对话
- 命令系统：支持 /** 格式的命令，如 /kb 切换知识库
"""

import time
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import QdrantCollectionNotFoundError, QdrantConnectionError, QdrantError
from app.core.logging import get_logger
from app.models.schemas.chat import (
    ChatMessage as ChatMessageSchema,
)
from app.models.schemas.chat import (
    ChatReference,
    ChatRequest,
    ChatResponse,
    ConversationSchema,
)
from app.models.schemas.search import SearchRequest
from app.providers.llm.factory import get_chat_llm, get_rewrite_llm
from app.repositories.conversation_repo import ConversationRepository
from app.services.compaction_service import HistoryCompactionService
from app.services.context_manager import ContextWindowManager
from app.services.vector_service import VectorService

logger = get_logger(__name__)

SYSTEM_PROMPT_TEMPLATE = """你是一个专业的知识库助手，请基于以下参考资料回答用户的问题。

参考资料：
{context}

要求：
1. 只使用参考资料中的信息回答问题，不要编造内容。
2. 如果参考资料中没有相关信息，请明确说明"根据现有资料无法回答该问题"。
3. 回答时请条理清晰，重点突出。
4. 引用的内容请尽量用自己的话组织，不要直接大段复制。
"""

# 知识库命中的相似度阈值（最高得分低于此值视为未命中）
KB_HIT_THRESHOLD = 0.6

# 未命中知识库时的警示信息（追加在答案末尾）
NON_KB_WARNING = """

> ⚠️ **提示**：当前问题未在知识库中检索到相关内容，以上回答由大模型直接生成，仅供参考，不保证准确性。
"""

# 警示信息的纯文本版本，用于从答案中剥离
_NON_KB_WARNING_PLAIN = NON_KB_WARNING.strip()

# 未命中知识库时，LLM 的系统提示（不注入检索上下文，让 LLM 自由回答）
DIRECT_LLM_SYSTEM_PROMPT = """你是一个专业的 AI 助手，请根据你的知识直接回答用户的问题。
回答时请条理清晰，重点突出。"""

REWRITE_PROMPT_TEMPLATE = """请根据对话历史，将用户的最新问题改写为一个独立、完整、语义明确的查询语句。

对话历史：
{history}

用户最新问题：{query}

请直接输出改写后的查询语句，不要包含任何解释或前缀。"""

# 支持的斜杠命令
SUPPORTED_COMMANDS = {
    "/kb": "切换或查看当前知识库",
    "/help": "查看可用命令",
    "/clear": "清空当前对话",
    "/reset": "重置对话（同 /clear）",
}


class ChatService:
    """对话服务

    Args:
        db: 主数据库会话（业务元数据，供 VectorService 等使用）
        chat_db: 对话数据库会话（会话/消息/摘要，独立存储）
    """

    def __init__(
        self,
        db: AsyncSession,
        chat_db: AsyncSession | None = None,
        visible_ids: list[str] | None = None,
    ):
        # 主库：业务元数据（集合/文档/向量索引等）
        self.db = db
        # 对话库：会话/消息/摘要（未指定则复用主库，保持兼容）
        self.chat_db = chat_db if chat_db is not None else db
        # SaaS 可见范围（None 表示不做过滤，内部/测试场景）
        self.visible_ids = visible_ids
        self.vector_service = VectorService(db)
        self.conv_repo = ConversationRepository(self.chat_db)
        self.context_mgr = ContextWindowManager()
        self.compaction_service = HistoryCompactionService(self.chat_db)
        # SmartQuery 智能问数编排器（懒加载，首次使用时创建）
        self._smart_query_orchestrator = None

    # ========== 智能 top_k 计算 ==========

    def _calc_vector_top_k(self, query: str, default_k: int = 5) -> int:
        """
        根据查询内容智能计算纯文本向量检索的 top_k。

        基于关键词规则快速判断（不调用LLM，性能可忽略），
        让不同类型的问题有不同的检索广度：

        - 列表/汇总/统计类：需要更多上下文 → 15~20
        - 对比/分析类：需要多角度信息 → 10~12
        - 总结/概括类：需要较全面的信息 → 8
        - 详情/解释/定义类：精准即可 → 5（默认）
        - 存在性/简单事实：1~3条就够 → 3

        返回值范围：[1, 20]
        """
        import re

        query_lower = query.lower().strip()
        if not query_lower:
            return default_k

        # 各类型的关键词模式（按优先级从高到低判断）
        patterns = [
            # --- 需要最多数据：列表 / 汇总 / 统计 / 全部 ---
            (
                20,
                [
                    r"有哪些|列出|列表|汇总|全部|所有|分别是|都是|都有|共有|合计|总计",
                    r"清单|明细|目录|名单|一览表",
                    r"多少种|多少个|有多少种|有几个",
                ],
            ),
            # --- 对比/分析类：需要多角度 ---
            (
                12,
                [
                    r"对比|比较|区别|差异|优劣势|优缺点",
                    r"分析|影响因素|为什么|原因",
                    r"关系|关联|联系",
                ],
            ),
            # --- 总结/概括类：较全面 ---
            (
                8,
                [
                    r"总结|概括|概述|简介|介绍一下|概况",
                    r"主要内容|核心要点|重点",
                    r"过程|步骤|流程|方法",
                ],
            ),
            # --- 存在性 / 简单事实：少量即可 ---
            (
                3,
                [
                    r"有没有|是否存在|是否有",
                    r"是什么|什么是|定义|含义|概念",
                    r"谁是|是哪个|哪一个",
                ],
            ),
        ]

        for k, pats in patterns:
            for pat in pats:
                if re.search(pat, query_lower):
                    # 越具体的问题类型，调整幅度越大
                    # 但不超过上限 20，不低于 1
                    return max(1, min(k, 20))

        # 兜底：默认值
        return max(1, min(default_k, 20))

    # ========== 对话主流程 ==========

    async def chat(self, req: ChatRequest, user_id: str | None = None) -> ChatResponse:
        """
        非流式对话
        """
        start_time = time.time()

        # 1. 解析命令（如果是斜杠命令，直接返回命令响应）
        cmd_result = self._parse_slash_command(req.query)
        if cmd_result:
            # 命令响应：不进行向量检索，直接返回命令结果
            return await self._handle_command_response(req, cmd_result, start_time)

        # 2. 获取或创建会话（持久化）
        conv, created = await self.conv_repo.get_or_create(
            session_id=req.session_id,
            collection_id=req.collection_id,
            user_id=user_id,
        )
        session_id = conv.session_id

        # 如果请求中没传 collection_id，但会话有，使用会话的
        effective_collection_id = req.collection_id or conv.collection_id

        # 计算当前轮次（现有消息数 / 2，每轮 user+assistant）
        current_turn = (conv.message_count // 2) + 1

        # 3. 加载历史消息 + 摘要
        history_msgs = await self._load_history(conv.id, session_id)
        # 用于问题改写的历史（移除警示语，避免干扰改写质量）
        history_for_rewrite = (
            await self._load_history(conv.id, session_id, strip_warning=True)
            if history_msgs
            else []
        )
        summaries = await self.compaction_service.get_summaries(conv.id)

        # 4. 问题改写（可选）
        query_for_search = req.query
        rewritten_query = None
        # 无论是否指定了知识库，都支持改写（因为未指定时会搜索所有知识库）
        if req.use_rewrite and history_for_rewrite:
            try:
                rewritten_query = await self._rewrite_query(req.query, history_for_rewrite)
                query_for_search = rewritten_query
                logger.debug(f"Query rewritten: '{req.query}' -> '{query_for_search}'")
            except Exception as e:
                logger.warning(f"Query rewrite failed: {e}, using original query")
                query_for_search = req.query

        # 5. 先尝试 SmartQuery 智能问数（如果启用）
        smart_query_result = None
        if settings.smart_query_enabled:
            try:
                smart_query_result = await self._try_smart_query(
                    query=query_for_search,
                    original_query=req.query,
                    collection_id=effective_collection_id or "all",
                    session_id=session_id,
                    history_msgs=history_msgs,
                    top_k=req.top_k,
                )
            except Exception as e:
                logger.warning(f"SmartQuery failed, falling back to regular RAG: {e}")
                smart_query_result = None

        # 如果 SmartQuery 成功返回了结果，直接使用
        if (
            smart_query_result
            and smart_query_result.get("success")
            and smart_query_result.get("answer")
        ):
            sq_answer = smart_query_result["answer"]
            sq_references = smart_query_result.get("references", [])
            sq_is_kb = smart_query_result.get("is_kb_answer", True)
            sq_used_sql = smart_query_result.get("used_sql", False)

            # 将 SmartQuery 的引用转换为 ChatReference 格式
            references = [
                ChatReference(
                    chunk_id=ref.get("chunk_id", ""),
                    document_id=ref.get("document_id", ""),
                    document_name=ref.get("document_name", ""),
                    chunk_index=ref.get("chunk_index", 0),
                    text=ref.get("text", ""),
                    score=ref.get("score", 0.0),
                    page_num=ref.get("page_num"),
                    collection_id=ref.get("collection_id", effective_collection_id),
                )
                for ref in sq_references
            ]

            # 保存消息并返回
            await self._save_turn(
                conv_id=conv.id,
                session_id=session_id,
                turn_id=current_turn,
                user_content=req.query,
                assistant_content=sq_answer,
            )

            # 触发压缩
            try:
                updated_history = history_msgs + [
                    {
                        "seq": conv.message_count - 2,
                        "turn_id": current_turn,
                        "role": "user",
                        "content": req.query,
                        "token_count": 0,
                        "is_key_turn": False,
                    },
                    {
                        "seq": conv.message_count - 1,
                        "turn_id": current_turn,
                        "role": "assistant",
                        "content": sq_answer,
                        "token_count": 0,
                        "is_key_turn": False,
                    },
                ]
                await self.compaction_service.maybe_compact(conv.id, session_id, updated_history)
            except Exception as e:
                logger.warning(f"Compaction after smart query failed (non-critical): {e}")

            latency_ms = (time.time() - start_time) * 1000
            return ChatResponse(
                session_id=session_id,
                answer=sq_answer,
                reasoning=smart_query_result.get("reasoning", ""),
                references=references,
                latency_ms=round(latency_ms, 2),
                model=smart_query_result.get("model", "smart-query"),
                is_kb_answer=sq_is_kb,
            )

        # 6. 向量检索（未指定 collection_id 时搜索所有知识库）
        #    （如果 SmartQuery 已经处理了，不会走到这里）
        search_result = None
        # 使用 "all" 表示跨所有知识库搜索；如果指定了则用指定的
        search_collection_id = effective_collection_id or "all"
        # 智能计算 top_k：根据查询类型动态调整
        vector_top_k = self._calc_vector_top_k(query_for_search, default_k=req.top_k)
        logger.debug(f"Vector search top_k: requested={req.top_k}, resolved={vector_top_k}")
        search_req = SearchRequest(
            query=query_for_search,
            top_k=vector_top_k,
            score_threshold=0.0,
        )
        try:
            search_result = await self.vector_service.search(search_collection_id, search_req, visible_ids=self.visible_ids)
        except (QdrantConnectionError, QdrantCollectionNotFoundError) as e:
            # Qdrant 不可达或集合不存在：降级为直接 LLM 回答
            logger.warning(f"Vector search unavailable, falling back to direct LLM answer: {e}")
            search_result = None
        except QdrantError as e:
            # 其他 Qdrant 错误：同样降级，保证可用性
            logger.warning(f"Vector search failed, falling back to direct LLM answer: {e}")
            search_result = None

        # 5.1 Fallback：如果改写后的查询检索未命中，用原始查询再试一次
        if rewritten_query and search_result and search_result.results:
            max_score = max((r.score for r in search_result.results), default=0.0)
            if max_score < KB_HIT_THRESHOLD:
                logger.info(
                    f"Rewritten query scored low ({max_score:.3f}), "
                    f"falling back to original query for search"
                )
                try:
                    fallback_req = SearchRequest(
                        query=req.query,
                        top_k=vector_top_k,
                        score_threshold=0.0,
                    )
                    fallback_result = await self.vector_service.search(
                        search_collection_id, fallback_req, visible_ids=self.visible_ids
                    )
                    if fallback_result and fallback_result.results:
                        fallback_max = max((r.score for r in fallback_result.results), default=0.0)
                        if fallback_max > max_score:
                            search_result = fallback_result
                            query_for_search = req.query
                            logger.debug(
                                f"Fallback search used, score improved: "
                                f"{max_score:.3f} -> {fallback_max:.3f}"
                            )
                except Exception as e:
                    logger.warning(f"Fallback search failed: {e}, keeping original result")

        # 6. 构造引用
        references = (
            [
                ChatReference(
                    chunk_id=r.chunk_id,
                    document_id=r.document_id,
                    document_name=r.document_name,
                    chunk_index=r.chunk_index,
                    text=r.text,
                    score=r.score,
                    page_num=r.page_num,
                    collection_id=r.collection_id,
                )
                for r in search_result.results
            ]
            if search_result
            else []
        )

        # 7. 判断是否命中知识库（未指定知识库时搜索全部，同样会有命中判断）
        is_kb_answer = False
        if references:
            max_score = max((r.score for r in references), default=0.0)
            if max_score >= KB_HIT_THRESHOLD:
                is_kb_answer = True
        if not references and search_result is None:
            reason = "vector search unavailable"
            logger.info(
                f"Knowledge base not hit ({reason}), "
                f"using direct LLM answer for query: '{req.query[:50]}...'"
            )
        elif not is_kb_answer:
            mode = (
                "all collections"
                if not effective_collection_id
                else f"collection {effective_collection_id}"
            )
            logger.info(
                f"Knowledge base not hit (low score, {mode}), "
                f"using direct LLM answer for query: '{req.query[:50]}...'"
            )

        # 8. 组装上下文（用 ContextWindowManager 做 token 预算和分层历史）
        if is_kb_answer:
            system_prompt = SYSTEM_PROMPT_TEMPLATE.format(context=self._build_context(references))
        else:
            system_prompt = DIRECT_LLM_SYSTEM_PROMPT

        assembly = self.context_mgr.assemble(
            system_prompt=system_prompt,
            current_query=req.query,
            history_messages=history_msgs,
            summaries=summaries,
        )

        # 9. 调用 LLM
        llm = get_chat_llm()
        try:
            resp = await llm.chat(
                assembly.messages,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            )
            answer = resp.content
            reasoning = resp.reasoning_content
            model = resp.model
        except Exception as e:
            logger.error(f"LLM chat failed: {e}", exc_info=True)
            answer = f"抱歉，生成回答时出错：{str(e)}"
            reasoning = ""
            model = ""

        # 10. 未命中知识库时追加警示信息
        final_answer = answer
        if not is_kb_answer and effective_collection_id:
            # 只有绑定了知识库但未命中时才加警示
            final_answer = answer + NON_KB_WARNING

        # 11. 持久化消息（先写 DB，再刷新 Redis 缓存）
        await self._save_turn(
            conv_id=conv.id,
            session_id=session_id,
            turn_id=current_turn,
            user_content=req.query,
            assistant_content=final_answer,
        )

        # 12. 触发历史压缩（按需）
        try:
            updated_history = history_msgs + [
                {
                    "seq": conv.message_count - 2,
                    "turn_id": current_turn,
                    "role": "user",
                    "content": req.query,
                    "token_count": 0,
                    "is_key_turn": False,
                },
                {
                    "seq": conv.message_count - 1,
                    "turn_id": current_turn,
                    "role": "assistant",
                    "content": final_answer,
                    "token_count": 0,
                    "is_key_turn": False,
                },
            ]
            await self.compaction_service.maybe_compact(conv.id, session_id, updated_history)
        except Exception as e:
            logger.warning(f"Compaction failed (non-critical): {e}")

        latency_ms = (time.time() - start_time) * 1000

        return ChatResponse(
            session_id=session_id,
            answer=final_answer,
            reasoning=reasoning,
            references=references,
            latency_ms=round(latency_ms, 2),
            model=model,
            is_kb_answer=is_kb_answer,
        )

    async def chat_stream(
        self, req: ChatRequest, user_id: str | None = None
    ) -> AsyncGenerator[dict, None]:
        """
        流式对话（SSE）

        注意：流式响应一旦开始 yield，HTTP 响应头就已发出。
        因此所有可能抛出异常的前置操作（会话加载、向量检索等）
        必须在生成器内部用 try-except 捕获，并以 error 事件形式返回，
        避免异常冒泡到 FastAPI 层导致 RuntimeError。
        """
        start_time = time.time()

        # ---------- 前置准备阶段（可能抛异常，需整体包裹） ----------
        try:
            # 1. 解析命令
            cmd_result = self._parse_slash_command(req.query)
            if cmd_result:
                # 命令响应：返回单条 done 事件
                yield {
                    "type": "metadata",
                    "session_id": req.session_id or "",
                    "references": [],
                    "is_kb_answer": False,
                    "is_command": True,
                    "command": cmd_result["command"],
                }
                yield {
                    "type": "content",
                    "content": cmd_result["response"],
                }
                yield {
                    "type": "done",
                    "answer": cmd_result["response"],
                    "reasoning": "",
                    "latency_ms": round((time.time() - start_time) * 1000, 2),
                    "model": "system",
                    "is_kb_answer": False,
                    "is_command": True,
                    "command": cmd_result["command"],
                }
                return

            # 2. 获取或创建会话
            conv, created = await self.conv_repo.get_or_create(
                session_id=req.session_id,
                collection_id=req.collection_id,
                user_id=user_id,
            )
            session_id = conv.session_id
            current_turn = (conv.message_count // 2) + 1

            effective_collection_id = req.collection_id or conv.collection_id

            # 3. 加载历史 + 摘要
            history_msgs = await self._load_history(conv.id, session_id)
            history_for_rewrite = (
                await self._load_history(conv.id, session_id, strip_warning=True)
                if history_msgs
                else []
            )
            summaries = await self.compaction_service.get_summaries(conv.id)

            # 4. 问题改写
            query_for_search = req.query
            rewritten_query = None
            # 无论是否指定了知识库，都支持改写
            if req.use_rewrite and history_for_rewrite:
                try:
                    rewritten_query = await self._rewrite_query(req.query, history_for_rewrite)
                    query_for_search = rewritten_query
                    logger.debug(f"Query rewritten: '{req.query}' -> '{query_for_search}'")
                except Exception as e:
                    logger.warning(f"Query rewrite failed: {e}, using original query")
                    query_for_search = req.query

            # 5. 先尝试 SmartQuery 智能问数（如果启用）
            smart_query_result = None
            if settings.smart_query_enabled:
                try:
                    smart_query_result = await self._try_smart_query(
                        query=query_for_search,
                        original_query=req.query,
                        collection_id=effective_collection_id or "all",
                        session_id=session_id,
                        history_msgs=history_msgs,
                        top_k=req.top_k,
                    )
                except Exception as e:
                    logger.warning(f"SmartQuery failed (stream), falling back to regular RAG: {e}")
                    smart_query_result = None

            # 如果 SmartQuery 成功返回了结果，直接流式返回
            if (
                smart_query_result
                and smart_query_result.get("success")
                and smart_query_result.get("answer")
            ):
                sq_answer = smart_query_result["answer"]
                sq_references = smart_query_result.get("references", [])
                sq_is_kb = smart_query_result.get("is_kb_answer", True)
                sq_used_sql = smart_query_result.get("used_sql", False)

                # 转换引用格式
                references = [
                    ChatReference(
                        chunk_id=ref.get("chunk_id", ""),
                        document_id=ref.get("document_id", ""),
                        document_name=ref.get("document_name", ""),
                        chunk_index=ref.get("chunk_index", 0),
                        text=ref.get("text", ""),
                        score=ref.get("score", 0.0),
                        page_num=ref.get("page_num"),
                        collection_id=ref.get("collection_id", effective_collection_id),
                    )
                    for ref in sq_references
                ]

                # 保存消息
                await self._save_turn(
                    conv_id=conv.id,
                    session_id=session_id,
                    turn_id=current_turn,
                    user_content=req.query,
                    assistant_content=sq_answer,
                )

                # 触发压缩
                try:
                    updated_history = history_msgs + [
                        {
                            "seq": conv.message_count - 2,
                            "turn_id": current_turn,
                            "role": "user",
                            "content": req.query,
                            "token_count": 0,
                            "is_key_turn": False,
                        },
                        {
                            "seq": conv.message_count - 1,
                            "turn_id": current_turn,
                            "role": "assistant",
                            "content": sq_answer,
                            "token_count": 0,
                            "is_key_turn": False,
                        },
                    ]
                    await self.compaction_service.maybe_compact(
                        conv.id, session_id, updated_history
                    )
                except Exception as e:
                    logger.warning(f"Compaction after smart query (stream) failed: {e}")

                # 流式输出（模拟流式，一次性输出完整答案）
                yield {
                    "type": "metadata",
                    "session_id": session_id,
                    "references": [r.model_dump() for r in references],
                    "is_kb_answer": sq_is_kb,
                    "is_command": False,
                    "model": smart_query_result.get("model", "smart-query"),
                    "used_sql": sq_used_sql,
                }
                yield {
                    "type": "content",
                    "content": sq_answer,
                }
                yield {
                    "type": "done",
                    "answer": sq_answer,
                    "reasoning": smart_query_result.get("reasoning", ""),
                    "latency_ms": round((time.time() - start_time) * 1000, 2),
                    "model": smart_query_result.get("model", "smart-query"),
                    "is_kb_answer": sq_is_kb,
                    "is_command": False,
                }
                return

            # 6. 向量检索（未指定 collection_id 时搜索所有知识库）
            #    （如果 SmartQuery 已经处理了，不会走到这里）
            search_result = None
            search_collection_id = effective_collection_id or "all"
            # 智能计算 top_k：根据查询类型动态调整
            vector_top_k = self._calc_vector_top_k(query_for_search, default_k=req.top_k)
            logger.debug(
                f"Vector search top_k (stream): requested={req.top_k}, resolved={vector_top_k}"
            )
            search_req = SearchRequest(
                query=query_for_search,
                top_k=vector_top_k,
                score_threshold=0.0,
            )
            try:
                search_result = await self.vector_service.search(search_collection_id, search_req, visible_ids=self.visible_ids)
            except (QdrantConnectionError, QdrantCollectionNotFoundError) as e:
                logger.warning(
                    f"Vector search unavailable, falling back to direct LLM answer (stream): {e}"
                )
                search_result = None
            except QdrantError as e:
                logger.warning(
                    f"Vector search failed, falling back to direct LLM answer (stream): {e}"
                )
                search_result = None

            # 5.1 Fallback：如果改写后的查询检索未命中，用原始查询再试一次
            if rewritten_query and search_result and search_result.results:
                max_score = max((r.score for r in search_result.results), default=0.0)
                if max_score < KB_HIT_THRESHOLD:
                    logger.info(
                        f"Rewritten query scored low ({max_score:.3f}), "
                        f"falling back to original query (stream)"
                    )
                    try:
                        fallback_req = SearchRequest(
                            query=req.query,
                            top_k=vector_top_k,
                            score_threshold=0.0,
                        )
                        fallback_result = await self.vector_service.search(
                            search_collection_id, fallback_req, visible_ids=self.visible_ids
                        )
                        if fallback_result and fallback_result.results:
                            fallback_max = max(
                                (r.score for r in fallback_result.results), default=0.0
                            )
                            if fallback_max > max_score:
                                search_result = fallback_result
                                query_for_search = req.query
                                logger.debug(
                                    f"Fallback search used (stream), score improved: "
                                    f"{max_score:.3f} -> {fallback_max:.3f}"
                                )
                    except Exception as e:
                        logger.warning(f"Fallback search failed (stream): {e}")

            references = (
                [
                    ChatReference(
                        chunk_id=r.chunk_id,
                        document_id=r.document_id,
                        document_name=r.document_name,
                        chunk_index=r.chunk_index,
                        text=r.text,
                        score=r.score,
                        page_num=r.page_num,
                        collection_id=r.collection_id,
                    )
                    for r in search_result.results
                ]
                if search_result
                else []
            )

            # 6. 判断是否命中知识库（未指定知识库时搜索全部，同样会有命中判断）
            is_kb_answer = False
            if references:
                max_score = max((r.score for r in references), default=0.0)
                if max_score >= KB_HIT_THRESHOLD:
                    is_kb_answer = True
            if not effective_collection_id and references:
                logger.info(f"Cross-collection search hit (stream): '{req.query[:50]}...'")
            elif not effective_collection_id and not references:
                logger.info(f"Cross-collection search no results (stream): '{req.query[:50]}...'")

        except Exception as e:
            # 前置阶段失败（非检索类错误，如 DB 故障等）：以 error 事件返回
            logger.error(f"Chat stream pre-check failed: {e}", exc_info=True)
            error_msg = f"请求处理失败：{str(e)}"
            yield {
                "type": "error",
                "message": error_msg,
                "code": "precheck_failed",
            }
            return

        # ---------- 流式输出阶段（从首个 yield 开始，HTTP 响应头已发送） ----------

        # 7. 输出 metadata 事件
        yield {
            "type": "metadata",
            "session_id": session_id,
            "references": [r.model_dump() for r in references],
            "is_kb_answer": is_kb_answer,
        }

        # 8. 组装上下文
        if is_kb_answer:
            system_prompt = SYSTEM_PROMPT_TEMPLATE.format(context=self._build_context(references))
        else:
            system_prompt = DIRECT_LLM_SYSTEM_PROMPT

        assembly = self.context_mgr.assemble(
            system_prompt=system_prompt,
            current_query=req.query,
            history_messages=history_msgs,
            summaries=summaries,
        )

        # 9. 流式调用 LLM
        llm = get_chat_llm()
        answer_parts: list[str] = []
        reasoning_parts: list[str] = []
        model = ""

        try:
            async for chunk in llm.chat_stream(
                assembly.messages,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            ):
                chunk_type = chunk.get("type", "content")
                chunk_content = chunk.get("content", "")
                if chunk_type == "reasoning":
                    reasoning_parts.append(chunk_content)
                    yield {"type": "reasoning", "content": chunk_content}
                else:
                    answer_parts.append(chunk_content)
                    yield {"type": "content", "content": chunk_content}
        except Exception as e:
            logger.error(f"LLM stream failed: {e}", exc_info=True)
            error_msg = f"抱歉，生成回答时出错：{str(e)}"
            yield {"type": "content", "content": error_msg}
            answer_parts = [error_msg]

        final_answer = "".join(answer_parts)
        final_reasoning = "".join(reasoning_parts)

        # 10. 未命中知识库时追加警示信息（只有绑定了知识库但未命中时）
        if not is_kb_answer and effective_collection_id:
            final_answer = final_answer + NON_KB_WARNING
            yield {"type": "content", "content": NON_KB_WARNING}

        # 11. 持久化消息
        try:
            await self._save_turn(
                conv_id=conv.id,
                session_id=session_id,
                turn_id=current_turn,
                user_content=req.query,
                assistant_content=final_answer,
            )
        except Exception as e:
            logger.warning(f"Save turn failed (non-critical): {e}")

        # 12. 触发压缩
        try:
            updated_history = history_msgs + [
                {
                    "seq": conv.message_count - 2,
                    "turn_id": current_turn,
                    "role": "user",
                    "content": req.query,
                    "token_count": 0,
                    "is_key_turn": False,
                },
                {
                    "seq": conv.message_count - 1,
                    "turn_id": current_turn,
                    "role": "assistant",
                    "content": final_answer,
                    "token_count": 0,
                    "is_key_turn": False,
                },
            ]
            await self.compaction_service.maybe_compact(conv.id, session_id, updated_history)
        except Exception as e:
            logger.warning(f"Compaction failed (non-critical): {e}")

        latency_ms = (time.time() - start_time) * 1000

        # 13. 输出 done 事件
        yield {
            "type": "done",
            "answer": final_answer,
            "reasoning": final_reasoning,
            "latency_ms": round(latency_ms, 2),
            "model": model,
            "is_kb_answer": is_kb_answer,
        }

    # ========== 斜杠命令处理 ==========

    def _parse_slash_command(self, query: str) -> dict | None:
        """解析斜杠命令（/** 格式）

        支持的命令：
        - /kb [知识库名称/ID]：切换或查看当前知识库
        - /help：查看帮助
        - /clear, /reset：清空对话（前端处理，这里返回指令）

        Returns:
            命令字典 {"command": "...", "response": "...", "action": "..."} 或 None
        """
        if not query or not query.strip().startswith("/"):
            return None

        parts = query.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            help_text = "**可用命令：**\n\n"
            for cmd_name, desc in SUPPORTED_COMMANDS.items():
                help_text += f"- `{cmd_name}` - {desc}\n"
            help_text += "\n**使用示例：**\n"
            help_text += "- `/kb 产品文档` - 切换到「产品文档」知识库\n"
            help_text += "- `/kb` - 查看当前绑定的知识库\n"
            help_text += "- `/clear` - 清空当前对话\n"
            return {"command": "/help", "response": help_text, "action": "help"}

        if cmd in ("/clear", "/reset"):
            return {
                "command": cmd,
                "response": "对话已清空，开始新的对话吧！",
                "action": "clear",
            }

        if cmd == "/kb":
            if not arg:
                return {
                    "command": "/kb",
                    "response": "请在命令后指定知识库名称或 ID，例如：`/kb 产品文档`",
                    "action": "kb_query",
                }
            return {
                "command": "/kb",
                "response": f"正在切换知识库到：**{arg}**\n\n（请在弹出的选择框中确认）",
                "action": "kb_switch",
                "target": arg,
            }

        # 未知命令
        return {
            "command": cmd,
            "response": f"未知命令：`{cmd}`\n\n输入 `/help` 查看可用命令。",
            "action": "unknown",
        }

    async def _handle_command_response(
        self, req: ChatRequest, cmd_result: dict, start_time: float
    ) -> ChatResponse:
        """处理命令响应（非流式）"""
        # 对于 clear/reset 命令，不创建新会话（前端自己处理）
        session_id = req.session_id or ""

        return ChatResponse(
            session_id=session_id,
            answer=cmd_result["response"],
            reasoning="",
            references=[],
            latency_ms=round((time.time() - start_time) * 1000, 2),
            model="system",
            is_kb_answer=False,
        )

    # ========== 会话管理接口 ==========

    async def list_all_conversations(
        self,
        limit: int = 20,
        offset: int = 0,
        user_id: str | None = None,
    ) -> tuple[list[ConversationSchema], int]:
        """列出所有会话（全局，按更新时间倒序），带总数

        Returns:
            (会话列表, 总数)
        """
        convs = await self.conv_repo.list_all(user_id=user_id, limit=limit, offset=offset)
        total = await self.conv_repo.count_all(user_id=user_id)
        return [ConversationSchema.from_db(c) for c in convs], total

    async def list_conversations(
        self,
        collection_id: str,
        limit: int = 50,
        offset: int = 0,
        user_id: str | None = None,
    ) -> list[ConversationSchema]:
        """列出会话列表（user_id 传入时仅返回本人会话）"""
        convs = await self.conv_repo.list_by_collection(
            collection_id=collection_id,
            user_id=user_id,
            limit=limit,
            offset=offset,
        )
        return [ConversationSchema.from_db(c) for c in convs]

    async def get_conversation(
        self, session_id: str, user_id: str | None = None
    ) -> ConversationSchema | None:
        """获取会话详情（含消息列表）

        user_id 传入时校验会话归属，非本人会话视为不存在。
        """
        conv = await self.conv_repo.get_by_session_id(session_id)
        if not conv:
            return None
        if user_id and conv.user_id and conv.user_id != user_id:
            return None
        msgs = await self.conv_repo.get_messages(conv.id, session_id)
        schema = ConversationSchema.from_db(conv)
        schema.messages = [ChatMessageSchema(role=m.role, content=m.content) for m in msgs]
        return schema

    async def delete_conversation(
        self, session_id: str, user_id: str | None = None
    ) -> bool:
        """删除（归档）会话

        user_id 传入时校验会话归属，非本人会话不允许删除。
        """
        if user_id:
            conv = await self.conv_repo.get_by_session_id(session_id)
            if not conv or (conv.user_id and conv.user_id != user_id):
                return False
        return await self.conv_repo.delete(session_id)

    # ========== 内部方法 ==========

    async def _load_history(
        self, conv_id: str, session_id: str, strip_warning: bool = False
    ) -> list[dict]:
        """加载历史消息为 dict 列表（供上下文管理器使用）

        Args:
            strip_warning: 是否移除 assistant 消息末尾的非知识库警示信息，
                           用于问题改写等场景，避免警示语干扰改写质量。
        """
        msgs = await self.conv_repo.get_messages(conv_id, session_id)
        result = []
        for m in msgs:
            content = m.content
            if strip_warning and m.role == "assistant":
                # 移除末尾的 NON_KB_WARNING 警示信息
                idx = content.rfind(NON_KB_WARNING.strip())
                if idx > 0:
                    content = content[:idx].rstrip()
            result.append(
                {
                    "seq": m.seq,
                    "turn_id": m.turn_id,
                    "role": m.role,
                    "content": content,
                    "token_count": m.token_count,
                    "is_key_turn": m.is_key_turn,
                }
            )
        return result

    async def _save_turn(
        self,
        conv_id: str,
        session_id: str,
        turn_id: int,
        user_content: str,
        assistant_content: str,
    ) -> None:
        """保存一轮对话（user + assistant）"""
        await self.conv_repo.append_message(
            conversation_id=conv_id,
            session_id=session_id,
            role="user",
            content=user_content,
            turn_id=turn_id,
            is_key_turn=False,
        )
        await self.conv_repo.append_message(
            conversation_id=conv_id,
            session_id=session_id,
            role="assistant",
            content=assistant_content,
            turn_id=turn_id,
            is_key_turn=False,
        )

    async def _rewrite_query(self, query: str, history_msgs: list[dict]) -> str:
        """根据历史会话改写用户问题"""
        if not history_msgs:
            return query

        # 取最近 6 条（3 轮）做改写
        recent = history_msgs[-6:]
        history_text = "\n".join([f"{m['role']}: {m['content']}" for m in recent])

        llm = get_rewrite_llm()
        result = await llm.simple_chat(
            prompt=REWRITE_PROMPT_TEMPLATE.format(history=history_text, query=query),
            system_prompt="你是一个专业的查询改写助手。",
            temperature=0.3,
            max_tokens=200,
        )
        return result.strip()

    def _build_context(self, references: list[ChatReference]) -> str:
        """从检索结果构造上下文文本"""
        if not references:
            return "（无相关参考资料）"

        parts: list[str] = []
        for i, ref in enumerate(references, 1):
            page_info = f"（第 {ref.page_num} 页）" if ref.page_num else ""
            parts.append(f"[{i}] 《{ref.document_name}》{page_info}\n{ref.text}")
        return "\n\n".join(parts)

    # ========== SmartQuery 智能问数 ==========

    def _get_smart_query_orchestrator(self):
        """获取 SmartQuery 编排器（懒加载）"""
        if self._smart_query_orchestrator is None:
            try:
                from app.services.smart_query import SmartQueryOrchestrator

                self._smart_query_orchestrator = SmartQueryOrchestrator(
                    db=self.db,
                    collection_id="",
                    visible_ids=self.visible_ids,
                )
            except Exception as e:
                logger.error(f"Failed to init SmartQueryOrchestrator: {e}")
                self._smart_query_orchestrator = None
        return self._smart_query_orchestrator

    async def _try_smart_query(
        self,
        query: str,
        original_query: str,
        collection_id: str,
        session_id: str,
        history_msgs: list[dict],
        top_k: int,
    ) -> dict | None:
        """
        尝试使用 SmartQuery 智能问数

        如果 SmartQuery 能处理这个问题，返回结果字典；
        否则返回 None，继续走原有的 RAG 流程。

        Returns:
            {
                "success": bool,
                "answer": str,
                "references": list,
                "is_kb_answer": bool,
                "used_sql": bool,
                "used_vector": bool,
                "reasoning": str,
                "model": str,
            }
            或 None（表示应该继续走原有流程）
        """
        orchestrator = self._get_smart_query_orchestrator()
        if orchestrator is None:
            return None

        try:
            # 使用原始问题（改写后的可能丢失实体信息）
            result = await orchestrator.query(
                user_query=original_query,
                collection_id=collection_id,
                session_id=session_id,
                history_messages=history_msgs,
                top_k=top_k,
            )

            # 判断是否应该使用 SmartQuery 的结果
            use_result = False

            # 情况1：SQL 查询成功且有数据
            if (
                result.used_sql
                and result.sql_result
                and result.sql_result.success
                and result.sql_result.rows
            ):
                use_result = True

            # 情况2：高置信度的结构化查询（即使无数据也用 SmartQuery 明确说明未找到）
            elif (
                result.query_plan
                and result.query_plan.intent.value == "structured"
                and result.query_plan.confidence >= 0.8
            ):
                use_result = True

            # 情况3：多意图查询（通过 sql_hits 判断），只要有任何一路有结果就使用
            elif hasattr(result, "sql_hits") and result.sql_hits and len(result.sql_hits) > 0:
                use_result = True

            # 情况4：向量检索有命中且 SmartQuery 生成了回答
            elif (
                result.used_vector
                and result.final_answer
                and result.final_answer != "未找到相关结果。"
            ):
                use_result = True

            else:
                # 语义类或低置信度且无结果 → 回退到原有的 RAG 流程
                return None

            if not use_result:
                return None

            # 返回 SmartQuery 结果
            return {
                "success": True,
                "answer": result.final_answer,
                "references": result.references,
                "is_kb_answer": result.is_kb_answer,
                "used_sql": result.used_sql,
                "used_vector": result.used_vector,
                "reasoning": result.query_plan.reasoning if result.query_plan else "",
                "model": "smart-query",
            }

        except Exception as e:
            logger.warning(f"SmartQuery execution failed: {e}")
            return None
