from openapiclient import OpenAPIClient
import asyncio

async def main():
    # Initialize the client with the OpenAPI definition
    api = OpenAPIClient(definition="https://petstore3.swagger.io/api/v3/openapi.json")

    try:
        # Initialize and get the dynamic client
        client = await api.init()
        print("client", client, dir(client))

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

