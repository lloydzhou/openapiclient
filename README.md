# OpenAPI Client for Python

A Python implementation inspired by [openapi-client-axios](https://github.com/openapistack/openapi-client-axios) that provides a dynamic client for OpenAPI specifications. This implementation uses httpx for HTTP requests and Python's metaprogramming capabilities to dynamically generate a client with an API design similar to httpx.

## Installation

```bash
pip install openapi-httpx-client        # library only
pip install "openapi-httpx-client[cli]" # library + `oapi` command
```

## CLI (`oapi`)

The optional `[cli]` extra installs an `oapi` command that turns any OpenAPI
spec into a CLI. Register an API once, then call its operations with generated
commands (parameter design follows the conventions of `gh` / `restish`):

```bash
oapi connect petstore https://petstore3.swagger.io/api/v3/openapi.json
oapi ls                                   # registered APIs
oapi schema petstore                      # list generated commands
oapi petstore get-pet-by-id 42            # required path params are positional
oapi petstore find-pets-by-status --status available
oapi petstore add-pet --body '{"name": "Rex", "photoUrls": []}'
oapi petstore add-pet -F name=Rex -F 'photoUrls=["https://x/r.png"]'
oapi api petstore GET /api/v3/store/inventory
```

Conventions:

- operation command names are kebab-case (`getPetById` -> `get-pet-by-id`);
  the raw `operationId` also works
- query/header parameters become `--kebab-case` flags; `enum` values are
  validated (`--status [available|pending|sold]`)
- request body: `--body` (inline JSON / `@file` / `-` stdin) or repeated
  `-F/--field key=value` (values parsed as JSON when possible)
- stdout carries data only; diagnostics go to stderr; non-2xx exits 1
- `--jq` filters output (`[].name`), `-o text` prints raw strings,
  `--dry-run` prints the request without sending it

## Usage

The client supports both synchronous and asynchronous usage patterns through a familiar context manager interface.

### Asynchronous Usage

```python
from openapiclient import OpenAPIClient
import asyncio

async def main():
    # Initialize the API factory with the OpenAPI definition
    api = OpenAPIClient(definition="https://petstore3.swagger.io/api/v3/openapi.json")
    
    # Use the async client with context manager
    async with api.AsyncClient() as client:
        # Show available operations
        print("Operations:", client.operations)
        print("Available functions:", client.functions)
        
        # Call operations directly as methods
        pet = await client.getPetById(petId=1)
        print(f"Status: {pet['status']}")
        print(f"Pet data: {pet['data']}")

        # Call operations directly as methods, using positional arguments, can using in path and query
        pet = await client.getPetById(1)
        print(f"Status: {pet['status']}")
        print(f"Pet data: {pet['data']}")
        
        # Alternative way to call methods
        pet = await client("getPetById", petId=2)
        print(f"Another pet: {pet['data']}")
        
        # Access AI tools definition for integration with LLMs
        print(f"AI tools: {client.tools}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Synchronous Usage

```python
from openapiclient import OpenAPIClient

# Initialize the API factory
api = OpenAPIClient(definition="https://petstore3.swagger.io/api/v3/openapi.json")

# Use the synchronous client with context manager
with api.Client() as client:
    # Show available operations
    print("Operations:", client.operations)
    
    # Call operations directly
    pet = client.getPetById(petId=1)
    print(f"Pet name: {pet['data'].get('name')}")
    
    # Call operations using dictionary-like access
    store_inventory = client["getInventory"]()
    print(f"Store inventory: {store_inventory['data']}")
```

### Advanced Options

You can pass any httpx client options when creating a client:

```python
# With timeout and custom headers
with api.Client(timeout=30, headers={"API-Key": "your-api-key"}) as client:
    result = client.someOperation()

# With proxy configuration
async with api.AsyncClient(proxies="http://localhost:8080") as client:
    result = await client.someOperation()
```

## Features

- Intuitive API design similar to httpx with context managers
- Support for both synchronous and asynchronous operations
- Dynamic client generation using Python metaprogramming
- Compatible with OpenAPI 3.0 and 3.1 specifications
- Support for loading specifications from URL, file, or dictionary (JSON/YAML)
- Response format similar to axios (data, status, headers, config)
- AI tools generation for integration with LLMs and AI assistants

## Client Properties

Each client instance provides these properties:

- `operations`: List of all available operation IDs
- `paths`: List of API paths defined in the specification
- `functions`: Dictionary of all operation methods mapped by name
- `tools`: List of AI function calling definitions for LLM integration

## Response Format

All API responses are returned in a dictionary format with the following keys:

- `data`: The parsed response body (JSON or text)
- `status`: HTTP status code
- `headers`: Response headers
- `config`: Original request configuration

## Author
lloydzhou


