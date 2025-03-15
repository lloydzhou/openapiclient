from openapiclient import OpenAPIClient
import asyncio


async def main():
    # Initialize the client with the OpenAPI definition
    api = OpenAPIClient(definition="https://petstore3.swagger.io/api/v3/openapi.json")

    try:
        with api.Client() as client:
            print("client.operations", client.operations)
            print("client.paths", client.paths)
            print("client.functions", client.functions)
            print("client.tools", client.tools)
            # Call an operation using the generated method
            response = client.getPetById(petId=1)

            # Print the response
            print(f"Status code: {response['status']}")
            print(f"Pet data: {response['data']}")

            # Call an operation using the generated method
            response = client('getPetById', petId=1)
            print(f"Status code: {response['status']}")
            print(f"Pet data: {response['data']}")

        async with api.AsyncClient() as client:
            print("client.operations", client.operations)
            print("client.paths", client.paths)
            print("client.functions", client.functions)
            print("client.tools", client.tools)
            # Call an operation using the generated method
            response = await client.getPetById(petId=1)

            # Print the response
            print(f"Status code: {response['status']}")
            print(f"Pet data: {response['data']}")

            # Call an operation using the generated method
            response = await client('getPetById', petId=1)
            print(f"Status code: {response['status']}")
            print(f"Pet data: {response['data']}")


    finally:
        pass

if __name__ == "__main__":
    asyncio.run(main())