"""百度搜索工具：基于百度千帆搜索 API 的 CrewAI 工具，支持时间范围/站点筛选。"""
import json
import logging
from typing import Type, Optional, List, Union, Literal

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, field_validator

from app.config import settings

logger = logging.getLogger("baidu_search")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class BaiduSearchInput(BaseModel):
    """百度搜索工具的输入参数模式"""
    query: str = Field(
        ...,
        description="搜索查询内容，即用户要搜索的问题或关键词，不能为空，不能只包含空白字符，通常由一个或几个词组成"
    )
    top_k: Optional[Union[int, str]] = Field(
        20,
        description="返回的搜索结果数量，默认20，在精确信息搜索时推荐5以下，广泛调研时10以上。"
    )
    recency_filter: Optional[Literal["week", "month", "semiyear", "year"]] = Field(
        None,
        description="根据网页发布时间进行筛选，可选值week(最近7天)、month(最近30天)、semiyear(最近180天)、year(最近365天)，通常根据用户需求的时效性要求来选择，常识性的问题不使用，资讯类的可能比较短。"
    )
    sites: Optional[List[str]] = Field(
        None,
        description="指定搜索的站点列表，最多支持20个站点，默认None，仅在设置的站点中进行内容搜索，示例['www.weather.com.cn', 'news.baidu.com']，通常根据需求指定权威站点，如词条类的通常是百度百科，股票类的通常是东方财富网，开源项目等通常是GitHub等。"
    )

    @field_validator('query')
    @classmethod
    def validate_query(cls, v: str) -> str:
        """验证查询内容不为空"""
        if not v or not v.strip():
            raise ValueError(
                "错误：查询内容不能为空。"
                "原因：输入的查询参数为空或只包含空白字符。"
                "解决提示：请提供有效的搜索关键词或问题。"
            )
        return v.strip()

    @field_validator('sites', mode='before')
    @classmethod
    def validate_sites(cls, v) -> Optional[List[str]]:
        """验证站点数量，并兼容 LLM 传入的字符串格式"""
        if isinstance(v, str):
            v_stripped = v.strip()
            if v_stripped in ("None", "null", "", "[]"):
                return None
            try:
                import json as _json
                parsed = _json.loads(v_stripped)
                if isinstance(parsed, list):
                    v = parsed
                else:
                    return None
            except (ValueError, TypeError):
                v = [s.strip().strip("'\"") for s in v_stripped.split(",") if s.strip()]
        if v and len(v) > 20:
            raise ValueError(
                f"错误：站点列表数量超出限制。"
                f"原因：当前提供了{len(v)}个站点，但最多只支持20个站点。"
                f"解决提示：请将站点列表减少到20个以内，例如只保留最关键的权威站点。"
            )
        return v

    @field_validator('top_k')
    @classmethod
    def validate_top_k(cls, v: Union[int, str]) -> int:
        """验证top_k范围，支持字符串输入自动转换为整数"""
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise ValueError(
                f"错误：top_k参数值无效。"
                f"原因：无法将值'{v}'转换为整数。"
                f"解决提示：请提供整数或可转换为整数的字符串，推荐值：精确信息搜索时5以下，广泛调研时10以上，默认20。"
            )
        if v < 0:
            raise ValueError(
                f"错误：top_k参数值无效。"
                f"原因：当前值{v}小于0，top_k必须大于等于0。"
                f"解决提示：请提供非负整数，推荐值：精确信息搜索时5以下，广泛调研时10以上，默认20。"
            )
        if v > 50:
            raise ValueError(
                f"错误：top_k参数值超出限制。"
                f"原因：当前值{v}大于50，web类型最大支持50条结果。"
                f"解决提示：请将top_k调整为50以内"
            )
        return v


class BaiduSearchTool(BaseTool):
    """
    百度搜索工具

    使用百度千帆搜索 API 进行网络搜索，支持网页搜索。
    需要百度千帆 API Key 进行鉴权。
    """
    name: str = "search_web"
    description: str = (
        "使用百度搜索引擎查找相关信息，可以按时间范围、指定站点等条件筛选搜索结果。"
        "获得包含标题、链接、内容摘要等详细信息的搜索结果。"
        "触发时机：当需要查找网络上的最新信息、特定网站内容、或按时间筛选搜索结果时使用，例如查找'Python最新版本特性'、'最近一周的AI新闻'、'特定网站的技术文档'等场景。"
        "适用边界：主要搜索一些通用公开的信息，当有其他专业工具能更精确查找内部或专业知识时，不使用该工具。"
    )
    args_schema: Type[BaseModel] = BaiduSearchInput

    max_results: int = 20

    def _run(
        self,
        query: str,
        top_k: Union[int, str] = 20,
        recency_filter: Optional[str] = None,
        sites: Optional[List[str]] = None,
    ) -> str:
        """执行百度搜索，返回格式化的搜索结果字符串。"""
        api_key = settings.BAIDU_API_KEY
        if not api_key:
            return (
                "错误：缺少API认证密钥。\n"
                "原因：未提供百度千帆 API Key，环境变量BAIDU_API_KEY未设置。\n"
                "解决提示：联系管理员设置环境变量BAIDU_API_KEY，或检查系统环境变量配置是否正确。\n"
            )

        # 确保 top_k 为整数，使用 max_results 作为默认
        top_k = int(top_k) if top_k != 20 else self.max_results
        logger.info("百度搜索: query=%s, top_k=%d, recency=%s, sites=%s", query, top_k, recency_filter, sites)

        # 构建请求体
        payload = {
            "messages": [
                {
                    "content": query,
                    "role": "user"
                }
            ],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [
                {"type": "web", "top_k": top_k}
            ],
        }

        if recency_filter:
            payload["search_recency_filter"] = recency_filter

        search_filter = {}
        if sites and isinstance(sites, list):
            search_filter["match"] = {"site": sites}
        if search_filter:
            payload["search_filter"] = search_filter

        url = "https://qianfan.baidubce.com/v2/ai_search/web_search"
        headers = {
            "X-Appbuilder-Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()

            result = response.json()

            # 检查错误
            error_code = result.get("code")
            if error_code is not None and error_code != 0 and error_code != "":
                error_msg = result.get("message", "未知错误")
                request_id = result.get("request_id") or result.get("requestId", "未知")
                error_descriptions = {
                    "400": "请求参数错误，请检查输入的参数是否正确",
                    "500": "服务器内部错误，可能是服务器临时故障，请稍后重试或尝试其它工具",
                    "501": "服务调用超时，请稍后重试或减少请求复杂度",
                    "502": "服务响应超时，请稍后重试或尝试其它工具",
                    "216003": "API Key认证失败，请检查API Key是否正确、是否已过期或是否有足够的权限",
                }
                error_hint = error_descriptions.get(str(error_code), "请检查请求参数是否正确，或稍后重试")
                logger.error("API错误: code=%s, msg=%s", error_code, error_msg)
                return (
                    f"错误：API返回错误。\n"
                    f"原因：百度搜索API返回错误码{error_code}，错误信息：{error_msg}，请求ID：{request_id}。\n"
                    f"解决提示：{error_hint}\n"
                )

            # 格式化搜索结果
            references = result.get("references", [])
            if not references:
                logger.warning("未找到搜索结果: query=%s", query)
                return (
                    f"错误：未找到相关搜索结果。\n"
                    f"原因：使用关键词'{query}'进行搜索，但未找到匹配的结果，可能是关键词过于具体、过滤条件过于严格或资源类型限制。\n"
                    f"解决提示：1)尝试使用不同的关键词或更通用的搜索词；2)检查是否使用了过于严格的过滤条件(如站点限制、时间范围等)，适当放宽条件。\n"
                )

            logger.info("搜索成功: %d 条结果", len(references))
            results = [f"找到 {len(references)} 条搜索结果", ""]

            for ref in references:
                ref_id = ref.get("id", "?")
                title = ref.get("title", "无标题")
                ref_url = ref.get("url", "")
                content = ref.get("content", "")

                results.append(f"结果{ref_id}: [ {title} ] ( {ref_url} ) \n  内容摘要: {content} \n")
                results.append("")

            return "\n".join(results)

        except requests.exceptions.Timeout:
            logger.error("请求超时")
            return (
                "错误：请求超时。\n"
                "原因：服务器响应时间超过30秒，可能是网络延迟、服务器繁忙或请求处理时间过长。\n"
                "解决提示：1)稍后重试搜索请求；2)如果问题持续，可能是服务器繁忙，建议稍后再试或联系技术支持。\n"
            )
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else "未知"
            logger.error("HTTP错误: status=%s, error=%s", status_code, e)
            return (
                f"错误：HTTP请求错误。\n"
                f"原因：HTTP请求失败，状态码{status_code}，错误详情：{str(e)}。\n"
                f"解决提示：出现请求错误，请尝试重试，反复出现请尝试其它工具\n"
            )
        except requests.exceptions.RequestException as e:
            logger.error("网络异常: %s - %s", type(e).__name__, e)
            return (
                f"错误：网络请求异常。\n"
                f"原因：网络请求过程中发生异常，错误类型：{type(e).__name__}，错误详情：{str(e)}。\n"
                f"解决提示：请尝试重试，反复出现请尝试其它工具\n"
            )
        except json.JSONDecodeError as e:
            logger.error("JSON解析错误: %s", e)
            return (
                "错误：响应解析错误。\n"
                f"原因：服务器返回的响应不是有效的JSON格式，错误详情：{str(e)}。\n"
                "解决提示：1)可能是服务器临时故障，请稍后重试；2)如果问题持续，请尝试其它工具。\n"
            )
        except Exception as e:
            logger.exception("未预期错误: %s - %s", type(e).__name__, e)
            return (
                f"错误：发生未预期的错误。\n"
                f"原因：程序执行过程中发生未预期的异常，错误类型：{type(e).__name__}，错误详情：{str(e)}。\n"
                f"解决提示：请检查输入参数是否正确，稍后重试，如果问题持续，请尝试其它工具。\n"
            )
