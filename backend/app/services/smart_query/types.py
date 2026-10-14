"""
SmartQuery 类型定义

统一的数据结构，用于各 Agent 之间的数据传递和状态管理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class QueryIntent(str, Enum):
    """查询意图分类"""

    STRUCTURED = "structured"
    """结构化查询：统计/列表/精确匹配/聚合，走 SQL"""

    SEMANTIC = "semantic"
    """语义查询：描述性/解释性/模糊查找，走向量检索"""

    HYBRID = "hybrid"
    """混合查询：既有结构化条件又有语义条件，两路检索后融合"""

    DIRECT_ANSWER = "direct_answer"
    """直接回答：简单事实/问候/系统问题，不需要检索"""


class QueryOperation(str, Enum):
    """结构化查询的操作类型"""

    COUNT = "count"
    """计数：有多少个"""

    LIST = "list"
    """列表：有哪些"""

    DETAIL = "detail"
    """详情：具体信息"""

    GROUP_COUNT = "group_count"
    """分组统计：按某维度分组计数"""

    SUM = "sum"
    """求和"""

    AVG = "avg"
    """平均值"""

    TOP_N = "top_n"
    """Top N 排名（行级：按某个数值列对原始记录排序取前 N 行）"""

    GROUP_AGG = "group_agg"
    """分组聚合排名（组级：按维度分组 + 聚合度量 + 组间排序）

    典型问题："哪个业务员销售的最多"、"销售额排名前三的客户"。
    与 TOP_N 的区别：TOP_N 对原始行排序，GROUP_AGG 先 GROUP BY 维度再聚合排序。
    """

    EXISTS = "exists"
    """是否存在"""

    UNKNOWN = "unknown"
    """未知操作"""


class SemanticType(str, Enum):
    """
    列的语义类型（用于跨表实体对齐和实体识别优化）。

    由规则法在 schema 提取阶段自动推断，供下游模块使用：
    - 跨表差异检测（优先按 semantic_type 对齐列）
    - 全局实体识别（知道某列存的是人/机构/地点等）
    - 多子任务优化（实体 → 候选列预过滤）
    """

    PERSON = "person"
    """人物：姓名、员工、客户联系人等"""

    ORGANIZATION = "organization"
    """组织/机构：公司、部门、客户名称、供应商等"""

    LOCATION = "location"
    """地点：城市、地区、地址等"""

    TIME = "time"
    """时间：日期、月份、年份、季度等"""

    AMOUNT = "amount"
    """金额：销售额、利润、费用等（货币类数值）"""

    QUANTITY = "quantity"
    """数量：个数、件数、重量、百分比等（非货币类数值）"""

    ID = "id"
    """标识符：编号、ID、工号、订单号等"""

    CATEGORY = "category"
    """分类/枚举：性别、状态、类型、等级等有限取值列"""

    OTHER = "other"
    """其他/未识别"""


@dataclass
class ColumnInfo:
    """列信息"""

    name: str
    """列名（原始列名，可能是中文）"""

    data_type: str = "VARCHAR"
    """数据类型：VARCHAR / INTEGER / FLOAT / DATE / DATETIME / BOOLEAN"""

    description: str = ""
    """列的业务含义描述（用于LLM理解）"""

    aliases: list[str] = field(default_factory=list)
    """别名/同义词，用于匹配用户问题中的不同说法"""

    sample_values: list[str] = field(default_factory=list)
    """示例值（用于LLM理解数据分布）"""

    is_hierarchy: bool = False
    """是否属于层级维度（如组织架构）"""

    hierarchy_level: int = 0
    """层级级别，数值越小级别越高（0=最高层）"""

    semantic_type: str = SemanticType.OTHER
    """列的语义类型（SemanticType 枚举），用于跨表实体对齐和实体识别优化"""


@dataclass
class TableSchema:
    """表结构信息"""

    table_name: str
    """表名"""

    sheet_name: str = ""
    """对应的 Excel Sheet 名"""

    columns: list[ColumnInfo] = field(default_factory=list)
    """列信息列表"""

    row_count: int = 0
    """预估行数"""

    description: str = ""
    """表的业务含义描述"""

    file_path: str = ""
    """源文件路径"""

    file_type: str = "xlsx"
    """文件类型"""

    document_id: str = ""
    """关联的文档ID"""

    def get_column(self, name: str) -> Optional[ColumnInfo]:
        """按列名获取列信息（精确匹配）"""
        for col in self.columns:
            if col.name == name:
                return col
        return None

    def find_column_by_alias(self, alias: str) -> Optional[ColumnInfo]:
        """按别名查找列（不区分大小写，包含模糊匹配）"""
        alias_lower = alias.lower().strip()
        # 先精确匹配列名
        for col in self.columns:
            if col.name.lower() == alias_lower:
                return col
        # 再匹配别名
        for col in self.columns:
            for a in col.aliases:
                if a.lower() == alias_lower:
                    return col
        # 最后模糊包含匹配
        for col in self.columns:
            if alias_lower in col.name.lower():
                return col
            for a in col.aliases:
                if alias_lower in a.lower():
                    return col
        return None

    def get_hierarchy_columns(self) -> list[ColumnInfo]:
        """获取所有层级维度列，按层级排序"""
        return sorted(
            [c for c in self.columns if c.is_hierarchy],
            key=lambda c: c.hierarchy_level,
        )


@dataclass
class FilterInfo:
    """过滤条件（强类型，替代 list[dict]）"""

    column: str
    """过滤列名（原始列名）"""

    value: str
    """过滤值"""

    operator: str = "="
    """比较运算符：= / != / > / < / >= / <= / LIKE"""

    column_alias: str = ""
    """列的中文别名（用于展示）"""

    entity_name: str = ""
    """对应的实体名称（如果是通过实体解析得到的）"""

    source: str = "explicit"
    """条件来源：explicit（本轮显式）/ inherited（历史继承）/ default（默认或业务规则）"""

    turn: int = 0
    """条件最初产生的轮次（用于状态追踪）"""

    explicit: bool = True
    """是否由用户显式指定（False 表示从历史继承而来）"""

    confidence: float = 1.0
    """该条件的置信度（0-1）"""


@dataclass
class EntityInfo:
    """实体信息（用于实体解析）"""

    name: str
    """实体名称（原始值）"""

    normalized_name: str
    """标准化后的名称"""

    entity_type: str
    """实体类型：对应列名，如 sales_director / salesperson"""

    column_name: str
    """所属的列名"""

    column_alias: str = ""
    """列的业务名称（中文）"""

    match_score: float = 1.0
    """匹配置信度 0-1"""

    hierarchy_level: int = -1
    """层级级别"""

    sample_context: str = ""
    """示例上下文（帮助LLM理解实体含义）"""


@dataclass
class QueryPlan:
    """查询计划（RouterAgent 的输出）"""

    intent: QueryIntent
    """查询意图"""

    operation: QueryOperation
    """操作类型（结构化查询时有效）"""

    target_column: str = ""
    """目标列（要统计/列出的对象）"""

    target_column_alias: str = ""
    """目标列的中文名称"""

    projection_column: str = ""
    """投影列（仅用于答案展示）

    表达"用户想看某个实体的哪个属性"，例如"胡兰桂在哪个部门" → 部门。
    只在合成阶段做定向回答时使用，**不参与检索与过滤条件构造**，
    因此不会与"实体同名列"冲突（该冲突正是 DETAIL 不使用 target_column 的原因）。
    """

    filters: list[FilterInfo] = field(default_factory=list)
    """过滤条件列表（强类型）"""

    group_by: str = ""
    """分组列（分组统计/分组聚合时）"""

    metric_column: str = ""
    """度量列（GROUP_AGG 时被聚合的数值列，如"销售金额"）

    与 target_column 区分：target_column 表示"统计/列出的对象"，
    metric_column 专用于 GROUP_AGG 的聚合度量，避免语义混用。
    """

    agg_func: str = "sum"
    """聚合函数（GROUP_AGG 时有效）：sum / avg / count / max / min"""

    order_by: str = ""
    """排序列"""

    order_dir: str = "DESC"
    """排序方向"""

    limit: int = 0
    """返回数量限制（0=不限制）"""

    entities: list[EntityInfo] = field(default_factory=list)
    """解析出的实体列表"""

    hierarchy_relation: str = ""
    """层级关系：如 "张浩手下的业务员" 表示 sales_director → salesperson"""

    reasoning: str = ""
    """决策推理过程（用于调试和日志）"""

    needs_sql: bool = False
    """是否需要 SQL 查询"""

    needs_vector: bool = False
    """是否需要向量检索"""

    confidence: float = 0.0
    """意图分类置信度 0-1"""

    suggested_top_k: int = 0
    """建议的检索数量（由 RouterAgent 根据查询类型智能决定，0=使用系统默认）

    不同查询类型的推荐值：
    - COUNT/SUM/AVG 聚合类：SQL 精确查询用 1，向量兜底用 20
    - LIST/GROUP_COUNT 列表类：20
    - TOP_N：从问题中提取 N
    - DETAIL 详情类：5
    - EXISTS 存在性：1
    - SEMANTIC 语义类：5（默认）
    """

    raw_response: str = ""
    """LLM 原始响应（用于调试）"""


@dataclass
class SQLQueryResult:
    """SQL 查询结果"""

    success: bool
    """是否成功"""

    sql: str = ""
    """执行的 SQL"""

    columns: list[str] = field(default_factory=list)
    """结果列名"""

    rows: list[list[Any]] = field(default_factory=list)
    """结果数据行"""

    row_count: int = 0
    """结果行数"""

    total_scanned: int = 0
    """扫描的总行数（用于验证数据完整性）"""

    error: str = ""
    """错误信息（失败时）"""

    execution_time_ms: float = 0.0
    """执行耗时（毫秒）"""

    retry_count: int = 0
    """重试次数"""

    def to_markdown_table(self, max_rows: int = 50) -> str:
        """将结果格式化为 Markdown 表格"""
        if not self.success or not self.columns:
            return ""
        lines = []
        # 表头
        lines.append("| " + " | ".join(self.columns) + " |")
        lines.append("| " + " | ".join(["---"] * len(self.columns)) + " |")
        # 数据行
        display_rows = self.rows[:max_rows]
        for row in display_rows:
            formatted = [str(v) if v is not None else "" for v in row]
            lines.append("| " + " | ".join(formatted) + " |")
        if len(self.rows) > max_rows:
            lines.append(f"\n_共 {len(self.rows)} 行，仅显示前 {max_rows} 行_")
        return "\n".join(lines)

    def get_single_value(self) -> Optional[Any]:
        """获取单个值（COUNT/SUM 等聚合查询用）"""
        if self.success and self.rows and len(self.rows) > 0 and len(self.rows[0]) > 0:
            return self.rows[0][0]
        return None


@dataclass
class RetrievalQueryResult:
    """向量检索结果"""

    success: bool = True
    """是否成功"""

    results: list[dict] = field(default_factory=list)
    """检索结果列表"""

    total: int = 0
    """结果总数"""

    max_score: float = 0.0
    """最高得分"""

    error: str = ""
    """错误信息"""

    execution_time_ms: float = 0.0
    """执行耗时（毫秒）"""


class RetrievalSource(str, Enum):
    """检索来源（多路并行）"""

    SQL = "sql"
    """结构化 SQL 查询（精确）"""

    VECTOR = "vector"
    """向量语义检索（模糊）"""

    KEYWORD = "keyword"
    """关键词全文检索（字面匹配）"""

    HYBRID = "hybrid"
    """混合检索结果（融合后）"""


@dataclass
class RetrievalHit:
    """单条检索命中（统一格式，各路结果都转换为这个）"""

    source: RetrievalSource
    """来源：sql / vector / keyword"""

    score: float = 0.0
    """相关性得分 0-1（sql 来源为 1.0，向量来源为相似度）"""

    rank: int = 0
    """在来源内部的排名"""

    record_id: str = ""
    """关联的记录 ID（如果有）"""

    document_id: str = ""
    """文档 ID"""

    sheet_name: str = ""
    """Sheet 名（Excel）"""

    row_number: int = -1
    """行号"""

    content: str = ""
    """文本内容（用于大模型参考）"""

    payload: dict = field(default_factory=dict)
    """原始数据 / 结构化字段"""

    metadata: dict = field(default_factory=dict)
    """额外元数据"""


@dataclass
class MultiRetrievalResult:
    """
    多路检索结果汇总

    包含每一路的独立结果，以及融合后的结果。
    """

    # 各路独立结果
    sql_hits: list[RetrievalHit] = field(default_factory=list)
    vector_hits: list[RetrievalHit] = field(default_factory=list)
    keyword_hits: list[RetrievalHit] = field(default_factory=list)

    # 融合后的结果
    merged_hits: list[RetrievalHit] = field(default_factory=list)

    # 各路执行状态
    sql_success: bool = True
    vector_success: bool = True
    keyword_success: bool = True

    sql_error: str = ""
    vector_error: str = ""
    keyword_error: str = ""

    sql_time_ms: float = 0.0
    vector_time_ms: float = 0.0
    keyword_time_ms: float = 0.0

    metadata: dict = field(default_factory=dict)
    """额外元数据（如多表查询标记、查询的表名列表等）"""

    @property
    def total_hits(self) -> int:
        """总命中数（融合后）"""
        return len(self.merged_hits)

    @property
    def sources_used(self) -> list[str]:
        """实际使用的检索源"""
        used = []
        if self.sql_hits:
            used.append("sql")
        if self.vector_hits:
            used.append("vector")
        if self.keyword_hits:
            used.append("keyword")
        return used

    def get_all_hits_by_source(self) -> dict[str, list[RetrievalHit]]:
        """按来源分组的所有命中"""
        return {
            "sql": self.sql_hits,
            "vector": self.vector_hits,
            "keyword": self.keyword_hits,
        }


@dataclass
class AgentContext:
    """Agent 执行上下文（在各 Agent 之间传递状态）"""

    query: str
    """原始用户问题"""

    collection_id: str = ""
    """知识库ID"""

    session_id: str = ""
    """会话ID"""

    history_messages: list[dict] = field(default_factory=list)
    """历史消息"""

    # 各阶段产物
    query_plan: Optional[QueryPlan] = None
    sql_result: Optional[SQLQueryResult] = None
    retrieval_result: Optional[RetrievalQueryResult] = None
    multi_retrieval: Optional[MultiRetrievalResult] = None
    """多路检索结果（新架构主用）"""

    # 最终输出
    final_answer: str = ""
    """最终答案"""

    references: list[dict] = field(default_factory=list)
    """引用来源"""

    is_kb_answer: bool = False
    """是否基于知识库回答"""

    used_sql: bool = False
    """是否使用了 SQL 查询"""

    used_vector: bool = False
    """是否使用了向量检索"""

    used_keyword: bool = False
    """是否使用了关键词检索"""

    effective_filters: list[dict] = field(default_factory=list)
    """本次查询实际生效的过滤条件（结构化）
    形如 [{"column": "客户", "value": "瑞丰机电有限公司", "operator": "="}]，
    供上层做多轮范围继承（会话结构化状态）。
    """

    effective_scope: dict = field(default_factory=dict)
    """本次生效的维度槽位（group_by / metric / order_dir / limit），供多轮维度继承。
    多意图路径下同样可用（取任一子查询的分组槽位）。
    """

    # 元数据
    total_time_ms: float = 0.0
    """总耗时"""

    error: str = ""
    """全局错误信息"""

    debug_info: dict = field(default_factory=dict)
    """调试信息"""
