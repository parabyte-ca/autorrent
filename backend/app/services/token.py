import hashlib
import hmac


def make_token(secret: str, password: str) -> str:
    return hmac.new(secret.encode(), password.encode(), hashlib.sha256).hexdigest()
