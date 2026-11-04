import os
from openai import OpenAI


BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def _get_client():
    api_key = os.getenv("ARK_API_KEY")
    model_id = os.getenv("ARK_MODEL_ID")

    if not api_key:
        raise RuntimeError("没有读取到 ARK_API_KEY，请先在CMD里设置环境变量。")

    if not model_id:
        raise RuntimeError("没有读取到 ARK_MODEL_ID，请先在CMD里设置模型ID/接入点ID。")

    client = OpenAI(
        api_key=api_key,
        base_url=BASE_URL
    )

    return client, model_id


def get_ai_group_summary(action_name, total_count, good_count, too_fast_count, range_small_count, swing_count, score):
    """
    生成一组训练后的 AI 教练总结。
    注意：不要每一次动作都调用，只建议做完一组后调用。
    """

    client, model_id = _get_client()

    prompt = f"""
你是一名智能健身教练。
用户刚完成一组训练，请根据数据给出一句简短中文建议，适合语音播报。

动作：{action_name}
总次数：{total_count}
标准次数：{good_count}
速度过快次数：{too_fast_count}
幅度不足次数：{range_small_count}
借力甩动次数：{swing_count}
当前得分：{score}

要求：
1. 50字以内
2. 不要分点
3. 语气像真人教练
4. 重点指出最主要的问题和下一组建议
"""

    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {
                "role": "system",
                "content": "你是智能健身教练，只输出简短、自然、适合语音播报的中文。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.4,
        max_tokens=120
    )

    return response.choices[0].message.content.strip()


if __name__ == "__main__":
    reply = get_ai_group_summary(
        action_name="二头弯举",
        total_count=10,
        good_count=7,
        too_fast_count=2,
        range_small_count=1,
        swing_count=0,
        score=85
    )

    print("AI教练总结：")
    print(reply)
