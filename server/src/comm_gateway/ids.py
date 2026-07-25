import secrets


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"
