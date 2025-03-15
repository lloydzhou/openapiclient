# OpenAPI Python 客户端

这是一个受 [openapi-client-axios](https://github.com/openapistack/openapi-client-axios) 启发的 Python 实现，为 OpenAPI 规范提供动态客户端。本实现使用 httpx 进行 HTTP 请求，并采用类似 httpx 的 API 设计，利用 Python 的元编程能力动态生成客户端。

## 安装

```bash
pip install openapi-httpx-client
```

## 使用方法

客户端支持通过熟悉的上下文管理器接口进行同步和异步使用。

### 异步使用

```python
from openapiclient import OpenAPIClient
import asyncio

async def main():
    # 使用 OpenAPI 定义初始化 API 工厂
    api = OpenAPIClient(definition="https://petstore3.swagger.io/api/v3/openapi.json")
    
    # 使用异步客户端和上下文管理器
    async with api.AsyncClient() as client:
        # 显示可用操作
        print("可用操作:", client.operations)
        print("可用函数:", client.functions)
        
        # 直接调用方法
        pet = await client.getPetById(petId=1)
        print(f"状态码: {pet['status']}")
        print(f"宠物数据: {pet['data']}")
        
        # 替代调用方式
        pet = await client("getPetById", petId=2)
        print(f"另一只宠物: {pet['data']}")
        
        # 访问AI工具定义，用于与LLM集成
        print(f"AI工具: {client.tools}")

if __name__ == "__main__":
    asyncio.run(main())
```

### 同步使用

```python
from openapiclient import OpenAPIClient

# 初始化API工厂
api = OpenAPIClient(definition="https://petstore3.swagger.io/api/v3/openapi.json")

# 使用同步客户端和上下文管理器
with api.Client() as client:
    # 显示可用操作
    print("可用操作:", client.operations)
    
    # 直接调用操作
    pet = client.getPetById(petId=1)
    print(f"宠物名称: {pet['data'].get('name')}")
    
    # 使用字典式访问调用操作
    store_inventory = client["getInventory"]()
    print(f"库存: {store_inventory['data']}")
```

### 高级选项

您可以在创建客户端时传递任何 httpx 客户端选项：

```python
# 设置超时和自定义头部
with api.Client(timeout=30, headers={"API-Key": "your-api-key"}) as client:
    result = client.someOperation()

# 配置代理
async with api.AsyncClient(proxies="http://localhost:8080") as client:
    result = await client.someOperation()
```

## 功能特点

- 类似于 httpx 的直观API设计，使用上下文管理器
- 支持同步和异步操作
- 使用 Python 元编程动态生成客户端
- 兼容 OpenAPI 3.0 和 3.1 规范
- 支持从 URL、文件或字典（JSON/YAML）加载规范
- 类似 axios 的响应格式（data, status, headers, config）
- 生成 AI 工具定义，便于与 LLM 和 AI 助手集成

## 客户端属性

每个客户端实例提供以下属性：

- `operations`：所有可用操作 ID 的列表
- `paths`：规范中定义的 API 路径列表
- `functions`：按名称映射的所有操作方法字典
- `tools`：用于 LLM 集成的 AI 函数调用定义列表

## 响应格式

所有 API 响应都以字典格式返回，包含以下键：

- `data`：解析后的响应体（JSON或文本）
- `status`：HTTP 状态码
- `headers`：响应头
- `config`：原始请求配置

## 作者
lloydzhou