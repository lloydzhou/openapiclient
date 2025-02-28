import httpx
import json
import os.path
from urllib.parse import urljoin, urlparse
import yaml
import types
import functools
from nanoid import generate as nanoid_generate

# Create a base class for DynamicClient
class DynamicClientBase:

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


# Create the main OpenAPIClient class
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
        self.definition = {}
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

    def get_operations(self):
        """
        Extract all operations from the OpenAPI definition.
        # https://github.com/openapistack/openapi-client-axios/blob/main/packages/openapi-client-axios/src/client.ts#L581

        Returns:
            list: A list of operation objects with normalized properties.
        """
        # Get all paths from the definition or empty dict if not available
        paths = self.definition.get('paths', {})
        # List of standard HTTP methods in OpenAPI
        http_methods = ['get', 'post', 'put', 'delete', 'patch', 'options', 'head']
        operations = []

        # Iterate through each path
        for path, path_object in paths.items():
            # For each HTTP method in the path
            for method in http_methods:
                operation = path_object.get(method)
                # Skip if this method doesn't exist for this path
                if not operation:
                    continue

                # Create operation object with basic properties
                op = operation.copy() if isinstance(operation, dict) else {}
                op['path'] = path
                op['method'] = method

                # Add path-level parameters if they exist
                if 'parameters' in path_object:
                    op['parameters'] = (op.get('parameters', []) + path_object['parameters'])

                # Add path-level servers if they exist
                if 'servers' in path_object:
                    op['servers'] = (op.get('servers', []) + path_object['servers'])

                # Set security from definition if not specified in operation
                if 'security' not in op and 'security' in self.definition:
                    op['security'] = self.definition['security']

                operations.append(op)

        return operations

    async def create_dynamic_client(self):
        """
        Create a client with methods dynamically generated from the OpenAPI spec using metaprogramming.
        
        Returns:
            DynamicClient: A client with methods for each operation in the spec
        """

        def resolve_schema_ref(schema):
            if '$ref' in schema:
                schema = all_references.get(schema['$ref'], {})
            elif schema.get('type') == 'object':
                for key, value in schema.get('properties', {}).items():
                    schema['properties'][key] = resolve_schema_ref(value)
            elif schema.get('type') == 'array':
                schema['items'] = resolve_schema_ref(schema.get('items', {}))
            return schema

        all_references = {f'#/components/schemas/{name}': schema for name, schema in self.definition.get('components', {}).get('schemas', {}).items()}
        # try to resolve sub `$ref` in all references
        for name, schema in all_references.items():
            schema = resolve_schema_ref(schema)
            all_references[name] = schema
            
        def create_tool(operation_id, operation):
            # Get parameters from the request body schema only for json content
            body = operation.get('requestBody', {})
            schema = body.get('content', {}).get('application/json', {}).get('schema', {})
            parameters = {
                "type": "object",
                "required": ['body'] if body.get("required", False) else [],
                "description": body.get('description', ''),
                "properties": {
                    "body": resolve_schema_ref(schema) if schema else {},
                }
            }
            # add parameters from path and query
            for parameter in operation.get('parameters', []):
                name = parameter.get('name')
                if parameter.get('required', False):
                    parameters["required"].append(name)
                item = {
                    "type": parameter.get('schema', {}).get('type', 'string'),
                    "description": parameter.get('description', ''),
                }
                # Add format, enum, and example if available
                for key in ['format', 'enum', 'example']:
                    if parameter.get('schema', {}).get(key):
                        item[key] = parameter.get('schema', {}).get(key)
                parameters["properties"][name] = item

            return {
                "type": "function",
                "function": {
                    "name": operation_id,
                    "description": operation.get('summary', '') or operation.get('description', ''),
                    "parameters": parameters,
                }
            }

        # Create a new class dynamically using type
        paths, tools, methods_dict = [], {}, {}
        for opration in self.get_operations():
            operation_id, path = opration.get('operationId'), opration.get('path')
            paths.append(path)
            methods_dict[operation_id] = self.create_operation_method(path, opration.get('method'), opration)
            tools[operation_id] = create_tool(operation_id, opration)

        # Generate a unique class name based on the API info or a random suffix
        api_title = self.definition.get('info', {}).get('title', '')
        # Replace spaces, hyphens, dots and other special characters
        api_version = self.definition.get('info', {}).get('version', '')

        if api_title:
            class_name = f"{api_title}Client_{api_version}" if api_version else f"{api_title}Client"
        else:
            # Fallback to a random suffix
            class_name = f"DynamicClient_{nanoid_generate(size=8)}"

        # Replace spaces, hyphens, dots and other special characters
        class_name = ''.join(c for c in class_name if c.isalnum())

        attribute_dict = {
            **methods_dict,
            'operations': list(methods_dict.keys()),
            'paths': paths,
            'tools': list(tools.values()),
            '_api': self,  # Store reference to the api
        }
        # Create the dynamic client class with the methods and the base class
        DynamicClientClass = type(class_name, (DynamicClientBase,), attribute_dict)

        # Create an instance of this class
        client = DynamicClientClass()

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

            # Make the request
            headers = kwargs.pop('headers', {})

            # Handle request body
            body = kwargs.pop('data', None) or kwargs.pop('body', None)
            # josn body
            if not body and len(kwargs) > 0 and operation.get('requestBody', {}).get('content', {}).get('application/json'):
                body = json.dumps(kwargs)

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
        
        operation_method.__name__ = operation.get('operationId', '')
        operation_method.__doc__ = operation.get('summary', '') + "\n\n" + operation.get('description', '')
        return operation_method
    
    async def close(self):
        """Close the HTTP session."""
        if self.session:
            await self.session.aclose()
