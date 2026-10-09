"""
文档结构化记录 ORM 模型（Excel Record 模式）

用于存储业务型 Excel 的完整结构化记录。
- 一行 = 一条 Record
- 数据以 JSON 存储，字段灵活，适应任意 Excel 结构
- 同时提取常见业务字段建索引，支持快速结构化查询

设计原则：
- SQLite 主存储：完整、准确、机器可查询
- Qdrant 语义索引：用于语义搜索找相关记录
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Column, String, Integer, BigInteger, Text, DateTime,
    Index, JSON,
)

from app.core.database import Base


class DocumentRecordDB(Base):
    """
    文档结构化记录

    对应 Excel 中的一行数据（Record 模式）。
    data_json 存储完整的结构化数据，
    常用字段提取到单独列中用于建索引和快速查询。
    """

    __tablename__ = "document_records"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # 归属
    document_id = Column(String(36), nullable=False, index=True)
    version_id = Column(String(36), nullable=False, index=True)
    collection_id = Column(String(36), nullable=False, index=True)

    # 位置信息
    sheet_name = Column(String(255), nullable=False, index=True)
    sheet_index = Column(Integer, nullable=False, default=0)
    row_number = Column(Integer, nullable=False)  # 0-based

    # 记录类型（如：sales_record / summary / meta，可用于分类）
    record_type = Column(String(50), nullable=True, index=True, default="data")

    # 完整结构化数据（JSON）
    data_json = Column(Text, nullable=False, default="{}")

    # 向量化文本（嵌入 Qdrant 用，这里也存一份方便溯源）
    vector_text = Column(Text, nullable=True)

    # Qdrant 中的点 ID（方便双向关联）
    qdrant_point_id = Column(String(64), nullable=True, index=True)

    # 常用业务字段（提取出来建索引，加速结构化查询）
    # 层级字段（销售组织常见）
    sales_director = Column(String(255), nullable=True, index=True)      # 销售总监
    region_manager = Column(String(255), nullable=True, index=True)      # 大区/特区经理
    region = Column(String(255), nullable=True, index=True)              # 大区
    marketing_company = Column(String(255), nullable=True, index=True)   # 营销公司/特区
    salesperson = Column(String(255), nullable=True, index=True)         # 业务员
    customer = Column(String(255), nullable=True, index=True)            # 客商/客户

    # 时间字段
    record_date = Column(String(32), nullable=True, index=True)          # 记录日期

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("idx_doc_sheet_row", "document_id", "sheet_name", "row_number", unique=True),
        Index("idx_doc_record_type", "document_id", "record_type"),
        Index("idx_hierarchy", "sales_director", "region_manager", "salesperson"),
    )

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------
    @property
    def data(self) -> Dict[str, Any]:
        """解析 data_json 为 dict"""
        if not self.data_json:
            return {}
        try:
            return json.loads(self.data_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    @data.setter
    def data(self, value: Dict[str, Any]):
        """设置 data（自动序列化 JSON 并提取常用字段）"""
        self.data_json = json.dumps(value, ensure_ascii=False)
        self._extract_common_fields(value)

    def _extract_common_fields(self, data: Dict[str, Any]):
        """从 data 中自动提取常用业务字段到索引列"""
        # 销售总监（各种可能的列名）
        self.sales_director = self._pick_field(
            data, ["销售总监", "总监", "sales_director", "director"]
        )
        # 特区经理 / 大区经理
        self.region_manager = self._pick_field(
            data, ["特区经理", "大区经理", "区域经理", "region_manager", "special_region_manager"]
        )
        # 大区
        self.region = self._pick_field(
            data, ["大区", "区域", "region", "sales_company", "销售公司"]
        )
        # 营销公司 / 特区
        self.marketing_company = self._pick_field(
            data, ["营销公司", "特区", "marketing_company", "special_region"]
        )
        # 业务员
        self.salesperson = self._pick_field(
            data, ["业务员", "销售员", "salesperson", "sales_man", "seller"]
        )
        # 客户 / 客商
        self.customer = self._pick_field(
            data, ["客商", "客户", "customer", "client", "公司名称"]
        )
        # 日期
        self.record_date = self._pick_field(
            data, ["日期", "date", "记录日期", "发货日期", "要货日期"]
        )

    @staticmethod
    def _pick_field(data: Dict[str, Any], candidates: List[str]) -> Optional[str]:
        """从多个候选列名中尝试取值"""
        for key in candidates:
            # 精确匹配
            if key in data and data[key]:
                val = data[key]
                if isinstance(val, str):
                    return val.strip()[:255]
                return str(val)[:255]
        return None
