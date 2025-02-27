# openapi-client-httpx
runtime openapi client based on httpx

## 参考
https://github.com/openapistack/openapi-client-axios  
https://openapistack.co/docs/openapi-client-axios/intro/

```
const api = new OpenAPIClientAxios({
  definition: "https://example.com/api/openapi.json",
});
api
  .init()
  .then((client) => client.getPetById(1))
  .then((res) => console.log("Here is pet id:1 from the api", res.data));
```

## 接口
```
from openapiclient import OpenAPIClient

async def main()
    api = OpenAPIClient(definition="https://example.com/api/openapi.json")
    client = await api.init()
    res = await client.getPetById()

```
