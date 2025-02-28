import os
from openapiclient import OpenAPIClient
from openai import AsyncOpenAI
import asyncio
import json

async def run_function_calling():
    # 初始化 OpenAI 客户端
    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1")
    )
    
    # 初始化 API 客户端
    api = OpenAPIClient(
        definition="https://ghfast.top/https://raw.githubusercontent.com/ChatGPTNextWeb/NextChat-Awesome-Plugins/refs/heads/main/plugins/jina-r/openapi.json"
    )
    
    try:
        # 获取动态生成的客户端
        api_client = await api.init()
        
        # 构建函数工具列表
        tools = api_client.tools
        
        # 创建对话
        messages = [
            {"role": "user", "content": "我想看看网页有什么内容： https://github.com/lloydzhou/openapiclient"}
        ]
        model = "deepseek-chat"
        
        # 调用 OpenAI API
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        
        # 获取 OpenAI 的响应
        assistant_message = response.choices[0].message

        print("\nOpenAI 回复:")
        print(assistant_message.content)
        
        # 如果有函数调用
        if assistant_message.tool_calls:
            tool_call = assistant_message.tool_calls[0]
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments)
            
            print(f"\n执行函数: {function_name}")
            print(f"参数: {json.dumps(function_args, indent=2, ensure_ascii=False)}")
            
            # 执行实际的 API 调用
            api_response = await api_client(function_name, **function_args)
            
            print("\nAPI 调用结果:")
            print(f"状态码: {api_response['status']}")
            print(f"响应数据: {json.dumps(api_response['data'], indent=2, ensure_ascii=False)}")
            
            # 将结果返回给 OpenAI
            messages.append(assistant_message)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(api_response['data'])
            })
            
            final_response = await client.chat.completions.create(
                model=model,
                messages=messages
            )
            
            print("\nOpenAI 最终回复:")
            print(final_response.choices[0].message.content)
            
    finally:
        await api.close()

if __name__ == "__main__":
    asyncio.run(run_function_calling())

