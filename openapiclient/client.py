import httpx
import json
import os.path
from urllib.parse import urljoin, urlparse
import yaml
import types
import functools
from nanoid import generate as nanoid_generate

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
        self.source_url = None  # Store the source URL if loaded from a URL

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

        # Set base URL from the servers list if available
        self.setup_base_url()

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
        self.source_url = self.definition_source  # Store the source URL
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

    def setup_base_url(self):
        """
        Set up the base URL for API requests, handling various server URL formats.
        """
        if 'servers' in self.definition and self.definition['servers']:
            server_url = self.definition['servers'][0]['url']

            # Check if this is a full URL or just a path
            parsed_url = urlparse(server_url)

            # If it's a full URL (has scheme), use it directly
            if parsed_url.scheme:
                self.base_url = server_url
            # If it's not a full URL and we loaded from a URL, combine them
            elif self.source_url:
                # Parse the source URL to get scheme, hostname, and port
                source_parsed = urlparse(self.source_url)
                base = f"{source_parsed.scheme}://{source_parsed.netloc}"

                # Combine the base with the server path
                self.base_url = urljoin(base, server_url)
            else:
                # Just use what we have
                self.base_url = server_url

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
                        def create_bound_method(func):
                            async def bound_method(*args, **kwargs):
                                return await func(*args, **kwargs)
                            # Set the name and docstring
                            bound_method.__name__ = operation_id
                            bound_method.__doc__ = operation.get('summary', '') + "\n\n" + operation.get('description', '')
                            return bound_method
                        
                        methods_dict[operation_id] = create_bound_method(method_func)

        # Generate a unique class name based on the API info or a random suffix
        api_title = self.definition.get('info', {}).get('title', '')
        # Replace spaces, hyphens, dots and other special characters
        api_version = self.definition.get('info', {}).get('version', '')

        if api_title:
            class_name = f"{api_title}Client_{api_version}" if api_version else f"{api_title}Client"
        else:
            # Fallback to a random suffix
            # Import nanoid on demand
            class_name = f"DynamicClient_{nanoid_generate(size=8)}"

        # Replace spaces, hyphens, dots and other special characters
        class_name = ''.join(c for c in class_name if c.isalnum())

        # Create the dynamic client class with the methods and the base class
        DynamicClientClass = type(class_name, (DynamicClientBase,), methods_dict)

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

            response = await self.session.request(
                method,
                full_url,
                params=query_params, 
                json=body, 
                headers=headers,
                **kwargs
            )

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

# Create the dynamic client class with the methods
# Create a base class for DynamicClient
class DynamicClientBase:
    @property
    def operations(self):
        """Return a list of all operation names from the OpenAPI definition"""
        ops = []
        for path, path_item in self._api.definition.get('paths', {}).items():
            for http_method, operation in path_item.items():
                if http_method in ['get', 'post', 'put', 'delete', 'patch', 'options', 'head']:
                    operation_id = operation.get('operationId')
                    if operation_id:
                        ops.append(operation_id)
        return ops
    
    @property
    def paths(self):
        """Return all paths from the OpenAPI definition"""
        return list(self._api.definition.get('paths', {}).keys())
    
    @property
    def tools(self):
        """Return all operations formatted as OpenAI function-calling tools"""
        tools = []
        
        # Use operations property for iteration
        for name in self.operations:
            # Get the operation details from the API definition
            operation = None
            for path, path_item in self._api.definition.get('paths', {}).items():
                for http_method, op in path_item.items():
                    if http_method in ['get', 'post', 'put', 'delete', 'patch'] and op.get('operationId') == name:
                        operation = op
                        break
                if operation:
                    break
            
            if not operation:
                continue
                
            # Create the function definition for OpenAI
            tool = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": operation.get('summary', '') or operation.get('description', ''),
                    "parameters": operation.get('requestBody', {}).get('content', {}).get(
                        'application/json', {}).get('schema', {})
                }
            }

            # Check if we have a request body schema with a reference and resolve it
            request_body_schema = operation.get('requestBody', {}).get('content', {}).get(
                'application/json', {}).get('schema', {})
            
            # Initialize parameters with request body schema (if exists)
            if request_body_schema:
                # If the schema is a reference, resolve it
                if '$ref' in request_body_schema:
                    request_body_schema = self.resolve_schema_ref(request_body_schema['$ref'])
                tool["function"]["parameters"] = request_body_schema
            
            # parameters = operation.get('parameters', [])
            # if parameters:
            #     properties = {}
            #     required = []
                
            #     for param in parameters:
            #         param_name = param.get('name')
            #         properties[param_name] = {
            #             "type": param.get('schema', {}).get('type', 'string'),
            #             "description": param.get('description', '')
            #         }
                    
            #         if param.get('required', False):
            #             required.append(param_name)
                
            #     # Update or create the parameters schema
            #     param_schema = {
            #         "type": "object",
            #         "properties": properties
            #     }
                
            #     if required:
            #         param_schema["required"] = required
                    
            #     tool["function"]["parameters"] = param_schema

            

            # # Check if the existing schema is a reference and resolve it
            # if 'parameters' in tool["function"]:
            #     if isinstance(tool["function"]["parameters"], dict) and '$ref' in tool["function"]["parameters"]:
            #         tool["function"]["parameters"] = self.resolve_schema_ref(tool["function"]["parameters"]['$ref'])
            
            tools.append(tool)
            
        return tools

    # If there are parameters defined in the operation, add them
    # Recursive function to resolve references in schemas
    def resolve_schema_ref(self, ref_path, visited=None):
        """Resolve a JSON schema reference to the actual schema object"""
        if visited is None:
            visited = set()
        
        # Prevent infinite recursion
        if ref_path in visited:
            return {"type": "object"}  # Return a simple schema to break the cycle
        
        visited.add(ref_path)
        
        # Handle only internal references for now
        if not ref_path.startswith('#/'):
            return {"type": "object", "description": f"External reference: {ref_path}"}
        
        # Parse the ref path
        path_parts = ref_path.replace('#/', '').split('/')
        
        # Navigate the definition to find the referenced schema
        current = self._api.definition
        for part in path_parts:
            if part not in current:
                return {"type": "object", "description": f"Invalid reference: {ref_path}"}
            current = current[part]
        
        # If the resolved schema has another reference, resolve it too
        if isinstance(current, dict) and '$ref' in current:
            return self.resolve_schema_ref(current['$ref'], visited)
        
        # Deep copy and resolve any nested references
        schema = json.loads(json.dumps(current))  # Deep copy to avoid modifying the original
        
        # Process nested references
        # Process the schema to resolve all nested references
        
        def process_schema(schema_obj, visited_set):
            if not isinstance(schema_obj, dict):
                return schema_obj
            
            result = {}
            # Special case: if we have a direct $ref, replace the entire object
            if '$ref' in schema_obj and isinstance(schema_obj['$ref'], str):
                return self.resolve_schema_ref(schema_obj['$ref'], visited_set.copy())
            
            # Otherwise process each field individually
            for key, value in schema_obj.items():
                if key == '$ref' and isinstance(value, str):
                    # Should be handled by the case above, but just in case
                    ref_result = self.resolve_schema_ref(value, visited_set.copy())
                    result.update(ref_result)
                elif isinstance(value, dict):
                    result[key] = process_schema(value, visited_set.copy())
                elif isinstance(value, list):
                    result[key] = [
                        process_schema(item, visited_set.copy()) if isinstance(item, dict) else item
                        for item in value
                    ]
                else:
                    result[key] = value
            
            return result

        # Process the entire schema to resolve all references
        schema = process_schema(schema, visited.copy())
        
        return schema
        
    @property
    def functions(self):
        """Return all operation methods available in this client"""
        return {name: getattr(self, name) for name in self.operations if hasattr(self, name)}
    
    def __getitem__(self, name):
        """Allow dictionary-like access to operations by name"""
        if name in self.operations and hasattr(self, name):
            return getattr(self, name)
        raise KeyError(f"Operation '{name}' not found")
        
    def __iter__(self):
        """Allow iteration over all operation names"""
        return iter(self.functions)
    
    def __call__(self, method_name, *args, **kwargs):
        """Allow calling methods by name with partial application"""
        if method_name not in self.operations:
            raise AttributeError(f"'{self.__class__.__name__}' has no operation '{method_name}'")
        
        method = getattr(self, method_name, None)
        if not method or not callable(method):
            raise AttributeError(f"'{self.__class__.__name__}' has no callable method '{method_name}'")
        
        return functools.partial(method, *args, **kwargs)
