"""集成测试公共辅助函数。"""
import uuid


def uniq(prefix: str) -> str:
    """生成唯一名字（agent/crew 的 name 有唯一约束）。"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def create_agent(client, name=None, memory=False, tool_ids=None, skill_ids=None):
    """便捷创建 Agent，返回响应 dict。"""
    payload = {
        "name": name or uniq("agent"),
        "role": "测试角色",
        "goal": "测试目标",
        "backstory": "测试背景",
        "memory": memory,
        "max_iter": 4,
    }
    if tool_ids is not None:
        payload["tool_ids"] = tool_ids
    if skill_ids is not None:
        payload["skill_ids"] = skill_ids
    r = client.post("/v1/agents", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def create_crew(client, name=None, agent_ids=None, manager_agent_id=None,
                process_type="sequential", tasks=None):
    """便捷创建 Crew，返回响应 dict。"""
    payload = {
        "name": name or uniq("crew"),
        "description": "测试描述",
        "process_type": process_type,
    }
    if agent_ids is not None:
        payload["agent_ids"] = agent_ids
    if manager_agent_id is not None:
        payload["manager_agent_id"] = manager_agent_id
    if tasks is not None:
        payload["tasks"] = tasks
    r = client.post("/v1/crews", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def cleanup_crew(client, crew_id):
    client.delete(f"/v1/crews/{crew_id}")


def cleanup_agent(client, agent_id):
    client.delete(f"/v1/agents/{agent_id}")
