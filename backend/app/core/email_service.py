"""
邮件服务 - 发送验证码邮件

策略：
- 配置了 SMTP（smtp_host + smtp_user + smtp_password）时，通过 aiosmtplib 异步发送
- 未配置 SMTP 时：开发模式下验证码写入日志并随接口返回（方便本地测试）；
  生产模式返回错误提示需要配置 SMTP
"""

import secrets
import string
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

import aiosmtplib

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

VERIFY_CODE_TEMPLATE = """【Vector Data Manager】邮箱验证码

您的验证码为：{code}

有效期 {minutes} 分钟。如非本人操作，请忽略此邮件。
"""


def generate_code(length: int = 6) -> str:
    """生成数字验证码（使用加密安全随机数）"""
    return "".join(secrets.choice(string.digits) for _ in range(length))


def smtp_configured() -> bool:
    """SMTP 是否已配置"""
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


async def send_code_email(to_email: str, code: str) -> bool:
    """异步发送验证码邮件

    Returns:
        True=发送成功 / False=未配置 SMTP（开发模式下验证码已写入日志）
    Raises:
        Exception: SMTP 已配置但发送失败
    """
    subject = "【Vector Data Manager】邮箱验证码"
    body = VERIFY_CODE_TEMPLATE.format(
        code=code, minutes=settings.email_code_expire_minutes
    )

    if not smtp_configured():
        # 开发模式：日志输出验证码，方便本地测试
        logger.warning(
            f"[DEV] SMTP not configured. Verification code for {to_email}: {code} "
            f"(valid {settings.email_code_expire_minutes} minutes)"
        )
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    from_addr = settings.smtp_from or settings.smtp_user
    msg["From"] = formataddr((str(Header("Vector Data Manager", "utf-8")), from_addr))
    msg["To"] = to_email

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        use_tls=settings.smtp_use_ssl,
        start_tls=settings.smtp_use_tls,
        timeout=15,
    )
    logger.info(f"Verification code email sent to {to_email}")
    return True
