import httpx
import json
import os.path
from urllib.parse import urljoin
import yaml
import types

class OpenAPIClient:
    """
    A Python client for OpenAPI specifications, inspired by openapi-client-axios.
    Uses httpx for HTTP requests.
    """
    
    def __init__(self, definition=None):
        """
        Initialize the OpenAPI client.
        
        Args:
            definition: URL or file path to the OpenAPI definition, or a dictionary containing the definition
        """
        self.definition_source = definition
        self.definition = None
        self.client = None
        self.base_url = ''
        self.session = None
    
    async def init(self):
        """
        Initialize the client by loading and parsing the OpenAPI definition.
        
        Returns:
            DynamicClient: A client with methods generated from the OpenAPI definition
        """
        # Load the OpenAPI definition
        await self.load_definition()
        
        # Create HTTP session
        self.session = httpx.AsyncClient()
        
        # Extract base URL from the servers list if available
        if 'servers' in self.definition and self.definition['servers']:
            self.base_url = self.definition['servers'][0]['url']
        
        # Create a dynamic client with methods based on the operations defined in the spec
        return await self.create_dynamic_client()
    
    async def load_definition(self):
        """
        Load the OpenAPI definition from a URL, file, or dictionary.
        """
        if isinstance(self.definition_source, dict):
            self.definition = self.definition_source
            return
        
        if os.path.isfile(str(self.definition_source)):
            # Load from file
            with open(self.definition_source, 'r') as f:
                content = f.read()
                if self.definition_source.endswith('.yaml') or self.definition_source.endswith('.yml'):
                    self.definition = yaml.safe_load(content)
                else:
                    self.definition = json.loads(content)
            return
        
        # Assume it's a URL
        async with httpx.AsyncClient() as client:
            response = await client.get(self.definition_source)
            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '')
                if 'yaml' in content_type or 'yml' in content_type:
                    self.definition = yaml.safe_load(response.text)
                else:
                    self.definition = response.json()
            else:
                raise Exception(f"Failed to load OpenAPI definition: {response.status_code}")
    
    async def create_dynamic_client(self):
        """
        Create a client with methods dynamically generated from the OpenAPI spec using metaprogramming.
        
        Returns:
            DynamicClient: A client with methods for each operation in the spec
        """
        # Create a new class dynamically using type
        methods_dict = {}
        
        # Generate methods for each path and operation
        paths = self.definition.get('paths', {})
        for path, path_item in paths.items():
            for method, operation in path_item.items():
                if method in ['get', 'post', 'put', 'delete', 'patch', 'options', 'head']:
                    operation_id = operation.get('operationId')
                    if operation_id:
                        # Create a method for this operation and capture it in the closure
                        method_func = self.create_operation_method(path, method, operation)
                        
                        # Create a function with proper binding
                        def create_bound_method(func=method_func):
                            async def bound_method(*args, **kwargs):
                                return await func(*args, **kwargs)
                            # Set the name and docstring
                            bound_method.__name__ = operation_id
                            bound_method.__doc__ = operation.get('summary', '') + "\n\n" + operation.get('description', '')
                            return bound_method
                        
                        methods_dict[operation_id] = create_bound_method()
        
        # Create the dynamic client class with the methods
        DynamicClientClass = type('DynamicClient', (object,), methods_dict)
        
        # Create an instance of this class
        client = DynamicClientClass()
        
        # Store reference to the api
        client._api = self
        
        return client
    
    def create_operation_method(self, path, method, operation):
        """
        Create a method for an operation defined in the OpenAPI spec.
        
        Args:
            path: The path template (e.g., "/pets/{petId}")
            method: The HTTP method (e.g., "get", "post")
            operation: The operation object from the OpenAPI spec
        
        Returns:
            function: A method that performs the API request
        """
        async def operation_method(*args, **kwargs):
            # Process path parameters
            url = path
            path_params = {}
            
            # Extract parameters from operation definition
            parameters = operation.get('parameters', [])
            for param in parameters:
                if param.get('in') == 'path':
                    name = param.get('name')
                    if name in kwargs:
                        path_params[name] = kwargs.pop(name)
            
            # Replace path parameters in the URL
            for name, value in path_params.items():
                url = url.replace(f"{{{name}}}", str(value))
            
            # Build the full URL
            full_url = urljoin(self.base_url, url)
            
            # Handle query parameters
            query_params = {}
            for param in parameters:
                if param.get('in') == 'query':
                    name = param.get('name')
                    if name in kwargs:
                        query_params[name] = kwargs.pop(name)
            
            # Handle request body
            body = kwargs.pop('data', None)
            
            # Make the request
            headers = kwargs.pop('headers', {})
            
            response = await self.session.request(method, 
                                               full_url, 
                                               params=query_params, 
                                               json=body, 
                                               headers=headers,
                                               **kwargs)
            
            if 'application/json' in response.headers.get('Content-Type', ''):
                result = response.json()
            else:
                result = response.text
            
            # Create response object similar to axios
            return {
                'data': result,
                'status': response.status_code,
                'headers': dict(response.headers),
                'config': kwargs
            }
        
        return operation_method
    
    async def close(self):
        """Close the HTTP session."""
        if self.session:
            await self.session.aclose()

