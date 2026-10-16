"""QQ API rejection evidence shared by normal sends and native streams."""


class QQAPIError(RuntimeError):
    """Preserve HTTP/business status without exposing response payloads to policy.

    Server/internal send failures can have landed. A documented business refusal
    in a successful HTTP response is different from ambiguous transport failure.
    """

    def __init__(self, status, path, code=None, message=""):
        self.status = status
        self.code = str(code) if code is not None else None
        self.definitely_rejected = (
            400 <= status < 500 and status != 408
        ) or (
            200 <= status < 300 and self.code not in (None, "0", "50001", "50055002")
        )
        super().__init__(f"QQ Bot API error [{status}] {path}: code={code} {message}")

