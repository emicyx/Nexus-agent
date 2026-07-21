"""全局自定义异常与错误码"""


class NexusError(Exception):
    """项目基础异常类"""

    code: str = "NEXUS_ERROR"

    def __init__(self, message: str = "", code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class LLMError(NexusError):
    code = "LLM_ERROR"


class CrewError(NexusError):
    code = "CREW_ERROR"
