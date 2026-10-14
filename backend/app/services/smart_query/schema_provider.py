"""
SchemaProvider - 表结构提供器

负责从 Excel/CSV 文件中提取表结构，建立：
1. 列信息（名称、类型、别名、描述）
2. 组织层级关系
3. 实体字典（人名/公司名等业务实体）
4. Forward Fill（合并单元格空值继承）

设计原则：
- 基于 DuckDB 直接查询，不重复造轮子
- 字段别名映射 + 层级关系显式建模
- 实体字典缓存，加速实体解析
- 支持 Forward Fill，解决 Excel 合并单元格问题
"""

from __future__ import annotations

import os
import uuid
from collections import OrderedDict

from app.core.logging import get_logger

from .semantic_packs import load_semantic_pack
from .types import ColumnInfo, EntityInfo, TableSchema

logger = get_logger(__name__)

# 最大缓存表数量（LRU 淘汰），防止内存无限增长
# 配置集中在 constants.py，这里只是引入
from .constants import MAX_CACHED_TABLES  # noqa: E402

# ---------------------------------------------------------------------------
# 注意：以下常量（HIERARCHY_KEYWORDS / COLUMN_ALIASES / COUNT_KEYWORDS 等）
# 已迁移到 semantic_packs/ 目录下的 YAML 配置文件。
# 这里保留一份模块级的"运行时加载值"，作为向后兼容的快捷访问。
# 新代码请通过 SchemaProvider 实例的语义包属性访问，
# 或者直接使用 semantic_packs.load_semantic_pack()。
# ---------------------------------------------------------------------------

# 当前加载的语义包名称（默认使用销售行业，与历史行为一致）
_current_pack_name: str = "sales"
_pack_loaded: dict = {}


def _get_pack() -> dict:
    """懒加载默认语义包"""
    global _pack_loaded
    if not _pack_loaded:
        _pack_loaded = load_semantic_pack(_current_pack_name)
    return _pack_loaded


# 向后兼容：导出旧的常量名，值来自默认语义包
def __getattr__(name):
    """模块级 __getattr__：兼容旧的常量导入方式"""
    if name == "HIERARCHY_KEYWORDS":
        return _get_pack()["hierarchy_levels"]
    if name == "COLUMN_ALIASES":
        return _get_pack()["column_aliases"]
    if name == "COUNT_KEYWORDS":
        return _get_pack()["intent_keywords"]["count"]
    if name == "LIST_KEYWORDS":
        return _get_pack()["intent_keywords"]["list"]
    if name == "GROUP_KEYWORDS":
        return _get_pack()["intent_keywords"]["group"]
    if name == "TOP_KEYWORDS":
        return _get_pack()["intent_keywords"]["top"]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


class SchemaProvider:
    """
    表结构提供器

    从 Excel/CSV 文件中提取结构化元数据，供 SQLAgent 和 RouterAgent 使用。
    """

    def __init__(
        self,
        max_cached_tables: int = MAX_CACHED_TABLES,
        industry: str = "sales",
    ):
        """
        Args:
            max_cached_tables: 最大缓存表数量（LRU 淘汰）
            industry: 业务语义包名称，默认 "sales"（销售行业）
                     可用值见 semantic_packs/ 目录下的 .yaml 文件
        """
        self._schema_cache: dict[str, TableSchema] = {}
        self._entities_cache: dict[str, list[EntityInfo]] = {}
        self._duckdb_conn = None
        # LRU 缓存：cache_key -> table_name，OrderedDict 实现 LRU
        self._table_lru: OrderedDict[str, str] = OrderedDict()
        self._max_cached_tables = max_cached_tables

        # 加载业务语义包
        self._industry = industry
        self._semantic_pack = load_semantic_pack(industry)
        logger.info(f"SchemaProvider initialized with semantic pack: '{industry}'")

    @property
    def semantic_pack(self) -> dict:
        """当前使用的业务语义包"""
        return self._semantic_pack

    @property
    def industry(self) -> str:
        """当前语义包名称"""
        return self._industry

    @property
    def hierarchy_levels(self) -> dict:
        """层级维度配置（等价于旧的 HIERARCHY_KEYWORDS）"""
        return self._semantic_pack["hierarchy_levels"]

    @property
    def column_aliases(self) -> dict:
        """列名同义词配置（等价于旧的 COLUMN_ALIASES）"""
        return self._semantic_pack["column_aliases"]

    @property
    def intent_keywords(self) -> dict:
        """查询意图关键词配置"""
        return self._semantic_pack["intent_keywords"]

    def switch_industry(self, industry: str) -> None:
        """切换业务语义包（运行时切换，清除相关缓存）"""
        if industry == self._industry:
            return
        self._semantic_pack = load_semantic_pack(industry)
        self._industry = industry
        # 语义包变了，schema 中的语义标签可能也变了，清空缓存
        self._schema_cache.clear()
        self._entities_cache.clear()
        self._table_lru.clear()
        logger.info(f"SchemaProvider switched to semantic pack: '{industry}'")

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def get_schema(self, file_path: str, sheet_name: str = "") -> TableSchema:
        """
        获取指定文件的表结构

        Args:
            file_path: Excel/CSV 文件的本地路径
            sheet_name: Sheet 名（Excel 专用，留空则用第一个 Sheet）

        Returns:
            TableSchema 对象
        """
        cache_key = f"{file_path}::{sheet_name}"
        if cache_key in self._schema_cache:
            return self._schema_cache[cache_key]

        schema = self._extract_schema(file_path, sheet_name)
        self._schema_cache[cache_key] = schema
        return schema

    def get_entities(self, file_path: str, sheet_name: str = "") -> list[EntityInfo]:
        """
        获取文件中的实体字典（所有维度列的去重值）

        用于 Entity Resolver 快速匹配用户提到的实体。
        """
        cache_key = f"{file_path}::{sheet_name}::entities"
        if cache_key in self._entities_cache:
            return self._entities_cache[cache_key]

        entities = self._extract_entities(file_path, sheet_name)
        self._entities_cache[cache_key] = entities
        return entities

    def find_entity(
        self,
        name: str,
        file_path: str,
        sheet_name: str = "",
        threshold: float = 0.6,
    ) -> EntityInfo | None:
        """
        在实体字典中查找指定名称的实体

        Args:
            name: 实体名称（用户输入的原始值）
            file_path: 文件路径
            sheet_name: Sheet 名
            threshold: 相似度阈值

        Returns:
            EntityInfo 或 None
        """
        entities = self.get_entities(file_path, sheet_name)
        normalized = self._normalize_text(name)

        if not normalized:
            return None

        # 精确匹配
        for ent in entities:
            if ent.normalized_name == normalized:
                return ent

        # 包含匹配（用户输入是实体名的子串，或实体名是用户输入的子串）
        # 取最长匹配优先，避免短实体误匹配（如"华东"优先于"华"）
        best_match: EntityInfo | None = None
        best_score = 0.0

        for ent in entities:
            ent_norm = ent.normalized_name
            score = 0.0

            if normalized == ent_norm:
                score = 1.0
            elif normalized in ent_norm or ent_norm in normalized:
                # 相似度 = 较短字符串长度 / 较长字符串长度
                # 长度越长且包含关系越完整，得分越高
                shorter = min(len(normalized), len(ent_norm))
                longer = max(len(normalized), len(ent_norm))
                base_score = shorter / longer if longer > 0 else 0.0
                # 长度权重：实体名 >=3 个字时加分，降低短名称误匹配率
                length_bonus = min(0.1, len(ent_norm) * 0.02)
                score = min(1.0, base_score + length_bonus)

            if score > best_score and score >= threshold:
                best_score = score
                best_match = EntityInfo(
                    name=ent.name,
                    normalized_name=ent.normalized_name,
                    entity_type=ent.entity_type,
                    column_name=ent.column_name,
                    column_alias=ent.column_alias,
                    match_score=score,
                    hierarchy_level=ent.hierarchy_level,
                    sample_context=ent.sample_context,
                )

        return best_match

    def match_column(self, keyword: str, schema: TableSchema) -> ColumnInfo | None:
        """
        根据关键词匹配列

        先匹配别名，再匹配列名，最后模糊匹配。
        """
        kw_normalized = self._normalize_text(keyword)
        if not kw_normalized:
            return None

        # 先从全局别名表查找（来自语义包）
        for col_name, aliases in self.column_aliases.items():
            if kw_normalized in [self._normalize_text(a) for a in aliases]:
                col = schema.find_column_by_alias(col_name)
                if col:
                    return col

        # 再在 schema 自身的别名中查找
        col = schema.find_column_by_alias(keyword)
        if col:
            return col

        return None

    def get_hierarchy_chain(
        self,
        schema: TableSchema,
        from_level: int,
        to_column: str,
    ) -> list[str]:
        """
        获取从一个层级到目标列的层级链

        例如：销售总监(level 0) → 业务员(level 4)
        返回 ["销售总监", "特区经理", "大区", "营销公司", "业务员"]
        """
        hierarchy_cols = schema.get_hierarchy_columns()
        if not hierarchy_cols:
            return []

        target_col = schema.get_column(to_column) or schema.find_column_by_alias(to_column)
        if not target_col:
            return []

        target_level = target_col.hierarchy_level

        chain = []
        for col in hierarchy_cols:
            if from_level <= col.hierarchy_level <= target_level:
                chain.append(col.name)

        return chain

    def get_table_name(self, file_path: str, sheet_name: str = "") -> str | None:
        """
        获取已加载的表名（如果已缓存）

        供 SQLAgent 等其他组件复用已加载的数据表，
        避免重复读取 Excel 和重复 forward fill。

        Returns:
            表名（如果已加载并在缓存中），否则 None
        """
        cache_key = f"{file_path}::{sheet_name}"
        if cache_key in self._table_lru:
            # 标记为最近使用（移到末尾）
            self._table_lru.move_to_end(cache_key)
            return self._table_lru[cache_key]
        return None

    def get_or_load_table(self, file_path: str, sheet_name: str = "") -> str:
        """
        获取表名，如果未加载则加载（供其他 Agent 复用数据）

        这是对外的主要复用接口：调用方只需指定文件路径，
        就能拿到可以直接查询的 DuckDB 表名。
        """
        table_name = self.get_table_name(file_path, sheet_name)
        if table_name:
            return table_name
        # 触发加载（走 schema 提取流程，会自动建表）
        self.get_schema(file_path, sheet_name)
        result = self.get_table_name(file_path, sheet_name)
        if not result:
            raise RuntimeError(f"Failed to load table for {file_path}")
        return result

    def clear_cache(self) -> None:
        """清空缓存"""
        self._schema_cache.clear()
        self._entities_cache.clear()
        self._table_lru.clear()
        if self._duckdb_conn:
            try:
                self._duckdb_conn.close()
            except Exception:
                pass
            self._duckdb_conn = None

    def _evict_if_needed(self) -> None:
        """LRU 淘汰：超出最大缓存表数时，淘汰最久未使用的"""
        while len(self._table_lru) > self._max_cached_tables:
            old_key, old_table = self._table_lru.popitem(last=False)
            try:
                conn = self._get_duckdb_conn()
                conn.execute(f'DROP TABLE IF EXISTS "{old_table}"')
                logger.debug(f"Evicted cached table: {old_table} ({old_key})")
            except Exception as e:
                logger.debug(f"Failed to evict table {old_table}: {e}")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_duckdb_conn(self):
        """获取 DuckDB 连接（懒加载）"""
        if self._duckdb_conn is not None:
            return self._duckdb_conn
        try:
            import duckdb

            self._duckdb_conn = duckdb.connect()
            return self._duckdb_conn
        except ImportError:
            logger.error("DuckDB not available, SchemaProvider cannot work properly")
            raise

    def _extract_schema(self, file_path: str, sheet_name: str = "") -> TableSchema:
        """从文件提取表结构"""
        conn = self._get_duckdb_conn()
        file_type = self._detect_file_type(file_path)
        safe_path = file_path.replace("'", "''")

        # 获取 Sheet 列表（Excel）
        actual_sheet = sheet_name
        if file_type in ("xlsx", "xls") and not actual_sheet:
            actual_sheet = self._get_first_sheet(conn, safe_path, file_path)

        # 使用 UUID 生成唯一表名，避免 hash 碰撞
        # 表名前缀用于识别来源，便于调试
        table_name = f"_sq_{uuid.uuid4().hex[:16]}"

        try:
            # 使用 pandas 做 Forward Fill（可靠处理多行空值的合并单元格场景）
            # 原因：DuckDB SQL 的窗口函数 forward fill 对连续多行空值处理不直观，
            # pandas ffill() 可以一次性处理所有空值，更可靠且代码简洁
            import pandas as pd

            # 读取为 DataFrame
            if file_type in ("xlsx", "xls"):
                df = pd.read_excel(file_path, sheet_name=actual_sheet)
            elif file_type == "csv":
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)

            # Forward Fill（处理合并单元格空值）
            df = df.ffill()

            # 去除全空行
            df = df.dropna(how="all")

            # 重置索引
            df = df.reset_index(drop=True)

            # 注册到 DuckDB
            conn.register(table_name, df)

            # 记录到 LRU 缓存（供其他 Agent 复用）
            cache_key = f"{file_path}::{sheet_name}"
            self._table_lru[cache_key] = table_name
            self._table_lru.move_to_end(cache_key)
            self._evict_if_needed()

            # 获取列信息
            cols_result = conn.execute(
                f"SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE table_name = '{table_name}' ORDER BY ordinal_position"
            ).fetchall()

            # 获取行数
            row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

            # 构造 ColumnInfo 列表
            columns: list[ColumnInfo] = []
            hierarchy_count = 0

            for col_name, data_type in cols_result:
                col_info = ColumnInfo(
                    name=col_name,
                    data_type=data_type,
                    description="",
                    aliases=self._get_aliases_for_column(col_name),
                    sample_values=[],
                    is_hierarchy=False,
                    hierarchy_level=0,
                )

                # 检测是否为层级维度列
                is_hier, level = self._detect_hierarchy_column(col_name, hierarchy_count)
                if is_hier:
                    col_info.is_hierarchy = True
                    col_info.hierarchy_level = level
                    hierarchy_count += 1

                # 获取示例值（前5个不重复值）
                try:
                    samples = conn.execute(
                        f'SELECT DISTINCT "{col_name}" FROM {table_name} '
                        f'WHERE "{col_name}" IS NOT NULL '
                        f"LIMIT 5"
                    ).fetchall()
                    col_info.sample_values = [str(r[0]) for r in samples if r[0] is not None]
                except Exception:
                    pass

                # 推断语义类型（基于列名 + 数据类型 + 样本值）
                col_info.semantic_type = self._infer_semantic_type(
                    col_name=col_name,
                    data_type=data_type,
                    sample_values=col_info.sample_values,
                )

                columns.append(col_info)

            schema = TableSchema(
                table_name=table_name,
                sheet_name=actual_sheet,
                columns=columns,
                row_count=row_count,
                description=f"Excel数据表，共 {row_count} 行，{len(columns)} 列",
                file_path=file_path,
                file_type=file_type,
            )

            return schema

        finally:
            # 表保留在 LRU 缓存中，供后续实体解析和 SQLAgent 复用
            # 超出最大缓存数时由 _evict_if_needed 自动淘汰
            pass

    def _extract_entities(self, file_path: str, sheet_name: str = "") -> list[EntityInfo]:
        """从文件提取实体字典"""
        schema = self.get_schema(file_path, sheet_name)
        conn = self._get_duckdb_conn()
        table_name = schema.table_name

        entities: list[EntityInfo] = []

        # 对每一个层级列，提取去重值作为实体
        for col in schema.columns:
            if not col.is_hierarchy:
                continue

            try:
                values = conn.execute(
                    f'SELECT DISTINCT "{col.name}" FROM {table_name} '
                    f'WHERE "{col.name}" IS NOT NULL '
                    f"AND TRIM(CAST(\"{col.name}\" AS VARCHAR)) != ''"
                ).fetchall()

                for row in values:
                    val = str(row[0]).strip()
                    if not val:
                        continue
                    normalized = self._normalize_text(val)
                    entities.append(
                        EntityInfo(
                            name=val,
                            normalized_name=normalized,
                            entity_type=col.name,
                            column_name=col.name,
                            column_alias=col.name,
                            match_score=1.0,
                            hierarchy_level=col.hierarchy_level,
                            sample_context=f"{col.name}：{val}",
                        )
                    )
            except Exception as e:
                logger.debug(f"Extract entities for column '{col.name}' failed: {e}")
                continue

        return entities

    def _get_first_sheet(self, conn, safe_path: str, file_path: str) -> str:
        """获取第一个 Sheet 的名称"""
        try:
            from openpyxl import load_workbook

            wb = load_workbook(file_path, read_only=True)
            sheets = wb.sheetnames
            wb.close()
            if sheets:
                return sheets[0]
        except Exception:
            pass

        return "Sheet1"

    def _detect_file_type(self, file_path: str) -> str:
        """检测文件类型"""
        ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        if ext in ("xlsx", "xls"):
            return "xlsx"
        elif ext == "csv":
            return "csv"
        return "xlsx"

    def _detect_hierarchy_column(
        self, col_name: str, current_hierarchy_count: int
    ) -> tuple[bool, int]:
        """
        检测列是否为层级维度列

        返回 (is_hierarchy, level)
        """
        col_normalized = self._normalize_text(col_name)

        # 先检查语义包中的层级关键词
        for keyword, level in self.hierarchy_levels.items():
            if keyword in col_normalized:
                return True, level

        # 启发式：如果列名包含"公司/部门/组/员/经理/总监/区域/大区"等词，
        # 且当前层级数较少，则视为层级列
        hierarchy_indicators = [
            "总监",
            "经理",
            "主管",
            "大区",
            "区域",
            "公司",
            "办事处",
            "部门",
            "组",
            "业务员",
            "销售",
            "客户",
            "专员",
            "主任",
            "负责人",
            # 人员相关（经常被查询的维度）
            "姓名",
            "员工",
            "人员",
            "员工编号",
            "工号",
            "雇员",
            # 常见业务维度
            "产品",
            "商品",
            "物料",
            "项目",
            "渠道",
        ]

        for indicator in hierarchy_indicators:
            if indicator in col_normalized:
                return True, current_hierarchy_count

        return False, 0

    def _get_aliases_for_column(self, col_name: str) -> list[str]:
        """获取列的别名列表"""
        col_normalized = self._normalize_text(col_name)

        # 从语义包的别名表反向查找
        aliases = []
        for std_name, alias_list in self.column_aliases.items():
            if col_normalized == self._normalize_text(std_name):
                aliases.extend(alias_list)
                break
            if any(col_normalized == self._normalize_text(a) for a in alias_list):
                aliases.append(std_name)
                aliases.extend([a for a in alias_list if a != col_name])
                break

        return list(set(aliases))

    def _normalize_text(self, value: str | None) -> str:
        """标准化文本：去空格、统一全角半角、小写"""
        if value is None:
            return ""

        text = str(value)

        # 全角空格 → 半角空格
        text = text.replace("\u3000", " ")

        # 全角字符 → 半角（简单处理常见字符）
        full_width = "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
        half_width = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        full_width += "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
        half_width += "abcdefghijklmnopqrstuvwxyz"
        full_width += "０１２３４５６７８９"
        half_width += "0123456789"

        for f, h in zip(full_width, half_width):
            text = text.replace(f, h)

        # 去除多余空白
        text = " ".join(text.split())

        # 去首尾空格
        text = text.strip()

        # 小写
        text = text.lower()

        return text

    # ------------------------------------------------------------------
    # 语义类型推断
    # ------------------------------------------------------------------

    def _infer_semantic_type(
        self,
        col_name: str,
        data_type: str,
        sample_values: list[str],
        unique_ratio: float | None = None,
    ) -> str:
        """
        根据列名、数据类型、样本值推断列的语义类型（SemanticType）。

        推断优先级：
        1. 数据类型强判断（DATE → TIME，BOOLEAN → CATEGORY 等）
        2. 列名关键词匹配（中文 + 英文常见词）
        3. 样本值数据特征兜底（唯一值少 → CATEGORY，含"公司/有限公司" → ORGANIZATION 等）

        Args:
            col_name: 列名
            data_type: DuckDB 返回的数据类型（大写，如 VARCHAR / INTEGER / DATE）
            sample_values: 样本值列表
            unique_ratio: 唯一值比例（可选，用于辅助判断 CATEGORY）

        Returns:
            SemanticType 枚举值字符串
        """
        from .types import SemanticType

        name_lower = col_name.lower()
        dt_upper = data_type.upper()

        # ---- 1. 数据类型强判断 ----
        if dt_upper in ("DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "DATETIME", "TIME"):
            return SemanticType.TIME
        if dt_upper in ("BOOLEAN", "BOOL"):
            return SemanticType.CATEGORY

        # 数值类 → 区分 AMOUNT / QUANTITY / ID
        if dt_upper in ("INTEGER", "BIGINT", "SMALLINT", "TINYINT",
                        "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL"):
            # 列名带金额关键词 → AMOUNT
            amount_keywords = [
                "金额", "销售额", "销售金额", "利润", "费用", "收入", "成本",
                "价格", "单价", "总额", "合计", "总计", "价款", "金额",
                "amount", "price", "cost", "revenue", "profit", "sales",
                "total", "fee", "money", "sum", "value",
            ]
            for kw in amount_keywords:
                if kw in name_lower:
                    return SemanticType.AMOUNT

            # 列名带 ID/编号 关键词 → ID
            id_keywords = [
                "编号", "工号", "单号", "序号", "编码", "号",
                "id", "code", "no", "sn", "num",
            ]
            # 注意："号" 单独太泛，只有在列名较短时才算（如 "订单号"）
            for kw in id_keywords:
                if kw == "号":
                    if name_lower.endswith("号") and len(name_lower) <= 6:
                        return SemanticType.ID
                    continue
                if kw in name_lower:
                    return SemanticType.ID

            # 其余数值 → QUANTITY
            return SemanticType.QUANTITY

        # ---- 2. 列名关键词匹配（VARCHAR 等字符串列） ----
        # PERSON
        person_keywords = [
            "姓名", "员工", "业务员", "销售员", "销售", "联系人", "人员",
            "经理", "总监", "主任", "主管", "负责人", "领导", "客户姓名",
            "名字", "代表", "顾问", "工程师",
            "name", "person", "people", "employee", "staff", "contact",
            "salesperson", "salesman", "manager", "director", "leader",
        ]
        for kw in person_keywords:
            if kw in name_lower:
                return SemanticType.PERSON

        # ORGANIZATION
        org_keywords = [
            "公司", "客户", "供应商", "部门", "单位", "机构", "企业",
            "厂商", "品牌", "客户名称", "公司名称", "供应商名称",
            "组织", "集团", "工厂", "办事处", "分公司", "事业部",
            "company", "organization", "department", "dept", "customer",
            "vendor", "supplier", "org", "brand", "factory", "division",
        ]
        for kw in org_keywords:
            if kw in name_lower:
                return SemanticType.ORGANIZATION

        # LOCATION
        location_keywords = [
            "城市", "地区", "地址", "省份", "国家", "区域", "地点",
            "省市", "大区", "片区", "省", "市", "区", "县",
            "city", "province", "address", "region", "area", "location",
            "country", "place", "zone", "district",
        ]
        for kw in location_keywords:
            if kw in name_lower:
                return SemanticType.LOCATION

        # TIME（字符串形式的时间列，如 "月份"、"年份"）
        time_keywords = [
            "日期", "时间", "月份", "年份", "季度", "周", "星期",
            "年月", "年度", "月度", "周期",
            "date", "time", "month", "year", "quarter", "week", "period",
        ]
        for kw in time_keywords:
            if kw in name_lower:
                return SemanticType.TIME

        # AMOUNT（字符串存储的金额，如带货币符号）
        amount_keywords_str = [
            "金额", "销售额", "销售金额", "利润", "费用", "收入", "成本",
            "价格", "单价", "总额", "合计", "总计",
            "amount", "price", "cost", "revenue", "profit", "sales",
            "total", "fee",
        ]
        for kw in amount_keywords_str:
            if kw in name_lower:
                return SemanticType.AMOUNT

        # QUANTITY（字符串存储的数量）
        qty_keywords = [
            "数量", "个数", "件数", "重量", "百分比", "比例", "比率",
            "数量", "台数", "人数", "次数",
            "quantity", "count", "number", "weight", "percent", "ratio",
            "qty", "num",
        ]
        for kw in qty_keywords:
            if kw in name_lower:
                return SemanticType.QUANTITY

        # ID
        id_keywords_str = [
            "编号", "ID", "工号", "单号", "编码", "序号", "证件号",
            "身份证", "电话", "手机", "邮箱",
            "id", "code", "no", "sn", "uuid",
        ]
        for kw in id_keywords_str:
            if kw.lower() in name_lower:
                return SemanticType.ID

        # CATEGORY
        cat_keywords = [
            "类型", "状态", "等级", "级别", "类别", "分类", "性别",
            "种类", "性质", "阶段", "渠道", "方式", "模式", "标签",
            "type", "status", "level", "category", "class", "gender",
            "kind", "phase", "channel", "mode", "tag", "flag",
        ]
        for kw in cat_keywords:
            if kw in name_lower:
                return SemanticType.CATEGORY

        # ---- 3. 样本值兜底判断 ----
        if sample_values:
            # 含"公司/有限公司/集团/分公司/工厂" → ORGANIZATION
            org_markers = ["公司", "有限公司", "集团", "分公司", "工厂", "办事处", "事业部"]
            if any(any(m in v for m in org_markers) for v in sample_values if v):
                return SemanticType.ORGANIZATION

            # 含"省/市/区/县"且长度适中 → LOCATION
            loc_markers = ["省", "市", "区", "县", "地区", "城市"]
            if any(any(m in v for m in loc_markers) for v in sample_values if v):
                return SemanticType.LOCATION

            # 日期格式样本 → TIME
            import re
            date_pattern = re.compile(r"^\d{4}[-/.年]\d{1,2}[-/.月]?\d{0,2}日?$")
            if any(date_pattern.match(str(v).strip()) for v in sample_values[:5] if v):
                return SemanticType.TIME

            # 唯一值很少 → CATEGORY（如果传了 unique_ratio 或从样本判断）
            if len(sample_values) >= 3 and len(set(sample_values)) <= min(10, len(sample_values) // 2):
                # 再排除像人名的（2-4个中文字符，且不含分类词）
                def _is_like_name(s: str) -> bool:
                    s = s.strip()
                    if 2 <= len(s) <= 4 and all("\u4e00" <= c <= "\u9fff" for c in s):
                        return True
                    return False

                name_like_count = sum(1 for v in sample_values if _is_like_name(v))
                if name_like_count < len(sample_values) * 0.5:
                    return SemanticType.CATEGORY

            # 字符串看起来像人名（短中文 + 高基数）→ PERSON
            name_like_count = sum(
                1 for v in sample_values
                if v.strip() and 2 <= len(v.strip()) <= 4
                and all("\u4e00" <= c <= "\u9fff" for c in v.strip())
            )
            if len(sample_values) >= 3 and name_like_count >= len(sample_values) * 0.7:
                return SemanticType.PERSON

        return SemanticType.OTHER
