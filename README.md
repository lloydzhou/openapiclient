# OpenAPI Client for Python

A Python implementation inspired by [openapi-client-axios](https://github.com/openapistack/openapi-client-axios) that provides a dynamic client for OpenAPI specifications. This implementation uses httpx for HTTP requests and Python's metaprogramming capabilities to dynamically generate a client.

## Installation

```bash
pip install openapi-httpx-client
```

## Usage

```python
from openapiclient import OpenAPIClient
import asyncio

async def main():
    # Initialize the client with the OpenAPI definition
    api = OpenAPIClient(definition="https://petstore3.swagger.io/api/v3/openapi.json")

    try:
        # Initialize and get the dynamic client
        client = await api.init()

        # Call an operation using the generated method
        response = await client.getPetById(petId=1)

        # Print the response
        print(f"Status code: {response['status']}")
        print(f"Pet data: {response['data']}")
    finally:
        # Close the HTTP session
        await api.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## Features

- Dynamic client generation using Python metaprogramming
- Supports OpenAPI 3.0 and 3.1
- Supports JSON and YAML specification formats
- Supports specification loading from URL, file, or dictionary
- Asynchronous API using httpx
- Response format similar to axios (data, status, headers, config)

## Author
lloydzhou (2025-02-27)


