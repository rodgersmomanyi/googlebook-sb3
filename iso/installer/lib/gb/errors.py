"""Flow outcomes (5.4). Pure."""


class FlowEnd(Exception):
    status = "OK"

    def __init__(self, code="-", msg=""):
        Exception.__init__(self, msg or code)
        self.code = code
        self.msg = msg


class Refused(FlowEnd):
    status = "REFUSED"


class Failed(FlowEnd):
    status = "FAILED"


class Cancelled(FlowEnd):
    status = "CANCELLED"

    def __init__(self, msg="cancelled"):
        FlowEnd.__init__(self, "-", msg)


class Back(Exception):
    """The user chose 'b' at a prompt."""


class Internal(AssertionError):
    """An invariant was violated (-> F_INTERNAL, nothing written by that call)."""
