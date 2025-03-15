import httpx
import json
import os.path
from typing import Dict, List, Any, Optional, Union, Callable, Type
from urllib.parse import urljoin, urlparse
import yaml
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

        return method(*args, **kwargs)


# Create the OpenAPIClient class
class OpenAPIClient:
    """
    A Python client for OpenAPI specifications, inspired by openapi-client-axios.
    Uses httpx for HTTP requests and supports both synchronous and asynchronous operations.
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
        self._is_async = False  # Flag to track if we're in async mode

    async def init(self):
        """
        Initialize the client asynchronously by loading and parsing the OpenAPI definition.
        Returns a client with asynchronous operation methods.

        Returns:
            DynamicClient: A client with async methods generated from the OpenAPI definition
        """
        # Set async flag
        self._is_async = True
        
        # Load the OpenAPI definition asynchronously
        await self._load_definition_async()

        # Create HTTP session
        self.session = httpx.AsyncClient()

        # Set base URL from the servers list if available
        self.setup_base_url()

        # Create a dynamic client with methods based on the operations defined in the spec
        return await self._create_dynamic_client()

    def init_sync(self):
        """
        Initialize the client synchronously by loading and parsing the OpenAPI definition.
        Returns a client with synchronous operation methods.

        Returns:
            DynamicClient: A client with sync methods generated from the OpenAPI definition
        """
        # Set async flag to False
        self._is_async = False
        
        # Load the OpenAPI definition synchronously
        self._load_definition_sync()

        # Create HTTP session
        self.session = httpx.Client()

        # Set base URL from the servers list if available
        self.setup_base_url()

        # Create a dynamic client with methods based on the operations defined in the spec
        return self._create_dynamic_client_sync()

    async def _load_definition_async(self):
        """
        Load the OpenAPI definition asynchronously from a URL, file, or dictionary.
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
                elif self.definition_source.endswith('.yaml') or self.definition_source.endswith('.yml'):
                    self.definition = yaml.safe_load(response.text)
                else:
                    self.definition = response.json()
            else:
                raise Exception(f"Failed to load OpenAPI definition: {response.status_code}")

    def _load_definition_sync(self):
        """
        Load the OpenAPI definition synchronously from a URL, file, or dictionary.
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
        with httpx.Client() as client:
            response = client.get(self.definition_source)
            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '')
                if 'yaml' in content_type or 'yml' in content_type:
                    self.definition = yaml.safe_load(response.text)
                elif self.definition_source.endswith('.yaml') or self.definition_source.endswith('.yml'):
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

    def resolve_schema_ref(self, schema, all_references):
        """Resolve schema references to their actual schema"""
        if '$ref' in schema:
            schema = all_references.get(schema['$ref'], {})
        elif schema.get('type') == 'object':
            for key, value in schema.get('properties', {}).items():
                schema['properties'][key] = self.resolve_schema_ref(value, all_references)
        elif schema.get('type') == 'array':
            schema['items'] = self.resolve_schema_ref(schema.get('items', {}), all_references)
        return schema

    def create_tool(self, operation_id, operation, all_references):
        """Create an AI tool description from operation data"""
        # Get parameters from the request body schema only for json content
        body = operation.get('requestBody', {})
        schema = body.get('content', {}).get('application/json', {}).get('schema', {})
        parameters = {
            "type": "object",
            "required": ['body'] if body.get("required", False) else [],
            "description": body.get('description', ''),
            "properties": {
                "body": self.resolve_schema_ref(schema, all_references) if schema else {},
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

    async def _create_dynamic_client(self):
        """
        Create an asynchronous client with methods dynamically generated from the OpenAPI spec.
        
        Returns:
            DynamicClient: A client with async methods for each operation in the spec
        """
        # Set up references dictionary
        all_references = {f'#/components/schemas/{name}': schema for name, schema in 
                         self.definition.get('components', {}).get('schemas', {}).items()}
        
        # Resolve all references
        for name, schema in all_references.items():
            schema = self.resolve_schema_ref(schema, all_references)
            all_references[name] = schema
        
        # Create methods, paths and tools
        paths, tools, methods_dict = [], {}, {}
        for operation in self.get_operations():
            operation_id = operation.get('operationId')
            path = operation.get('path')
            paths.append(path)
            methods_dict[operation_id] = self._create_async_operation_method(path, operation.get('method'), operation)
            tools[operation_id] = self.create_tool(operation_id, operation, all_references)

        # Generate class name
        class_name = self._generate_client_class_name()

        # Create dynamic class attributes
        attribute_dict = {
            **methods_dict,
            'operations': list(methods_dict.keys()),
            'paths': paths,
            'tools': list(tools.values()),
            '_api': self,  # Store reference to the api
        }
        
        # Create the dynamic client class
        DynamicClientClass = type(class_name, (DynamicClientBase,), attribute_dict)

        # Create an instance of this class
        client = DynamicClientClass()
        return client

    def _create_dynamic_client_sync(self):
        """
        Create a synchronous client with methods dynamically generated from the OpenAPI spec.
        
        Returns:
            DynamicClient: A client with sync methods for each operation in the spec
        """
        # Set up references dictionary
        all_references = {f'#/components/schemas/{name}': schema for name, schema in 
                         self.definition.get('components', {}).get('schemas', {}).items()}
        
        # Resolve all references
        for name, schema in all_references.items():
            schema = self.resolve_schema_ref(schema, all_references)
            all_references[name] = schema
        
        # Create methods, paths and tools
        paths, tools, methods_dict = [], {}, {}
        for operation in self.get_operations():
            operation_id = operation.get('operationId')
            path = operation.get('path')
            paths.append(path)
            methods_dict[operation_id] = self._create_sync_operation_method(path, operation.get('method'), operation)
            tools[operation_id] = self.create_tool(operation_id, operation, all_references)

        # Generate class name
        class_name = self._generate_client_class_name()

        # Create dynamic class attributes
        attribute_dict = {
            **methods_dict,
            'operations': list(methods_dict.keys()),
            'paths': paths,
            'tools': list(tools.values()),
            '_api': self,  # Store reference to the api
        }
        
        # Create the dynamic client class
        DynamicClientClass = type(class_name, (DynamicClientBase,), attribute_dict)

        # Create an instance of this class
        client = DynamicClientClass()
        return client

    def _generate_client_class_name(self):
        """Generate a class name based on the API info"""
        api_title = self.definition.get('info', {}).get('title', '')
        api_version = self.definition.get('info', {}).get('version', '')

        if api_title:
            class_name = f"{api_title}Client_{api_version}" if api_version else f"{api_title}Client"
        else:
            # Fallback to a random suffix
            class_name = f"DynamicClient_{nanoid_generate(size=8)}"

        # Replace spaces, hyphens, dots and other special characters
        class_name = ''.join(c for c in class_name if c.isalnum())
        return class_name

    def _create_async_operation_method(self, path, method, operation):
        """
        Create an async method for an operation defined in the OpenAPI spec.
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
            # json body
            if not body and len(kwargs) > 0 and operation.get('requestBody', {}).get('content', {}).get('application/json'):
                body = kwargs

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

    def _create_sync_operation_method(self, path, method, operation):
        """
        Create a synchronous method for an operation defined in the OpenAPI spec.
        """
        def operation_method(*args, **kwargs):
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
            # json body
            if not body and len(kwargs) > 0 and operation.get('requestBody', {}).get('content', {}).get('application/json'):
                body = kwargs

            response = self.session.request(
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
        """Close the HTTP session if it's an async session."""
        if self.session:
            if self._is_async:
                await self.session.aclose()
            else:
                self.session.close()