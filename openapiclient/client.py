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


class BaseClient:
    """Base class for OpenAPI clients with common functionality"""
    
    def __init__(self, api, session=None):
        self.api = api
        self.session = session
        self.client = None

    def setup_base_url(self):
        """Set up the base URL for API requests"""
        if 'servers' in self.api.definition and self.api.definition['servers']:
            server_url = self.api.definition['servers'][0]['url']
            
            parsed_url = urlparse(server_url)
            
            if parsed_url.scheme:
                self.api.base_url = server_url
            elif self.api.source_url:
                source_parsed = urlparse(self.api.source_url)
                base = f"{source_parsed.scheme}://{source_parsed.netloc}"
                self.api.base_url = urljoin(base, server_url)
            else:
                self.api.base_url = server_url


class Client(BaseClient):
    """Synchronous OpenAPI client"""
    
    def __init__(self, api, **kwargs):
        super().__init__(api)
        self.session = httpx.Client(**kwargs) if not self.session else self.session
        self.client = None
        
    def __enter__(self):
        """Enter context manager and initialize the client"""
        if not self.api.definition:
            self.api._load_definition_sync()
            
        self.setup_base_url()
        self.client = self.api._create_client(self, is_async=False)
        return self.client
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager and close resources"""
        if self.session:
            self.session.close()


class AsyncClient(BaseClient):
    """Asynchronous OpenAPI client"""
    
    def __init__(self, api, **kwargs):
        super().__init__(api)
        self.session = httpx.AsyncClient(**kwargs) if not self.session else self.session
        self.client = None
        
    async def __aenter__(self):
        """Enter async context manager and initialize the client"""
        if not self.api.definition:
            await self.api._load_definition_async()
            
        self.setup_base_url()
        self.client = self.api._create_client(self, is_async=True)
        return self.client
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context manager and close resources"""
        if self.session:
            await self.session.aclose()


# Create the main OpenAPIClient class as a factory
class OpenAPIClient:
    """
    A Python client for OpenAPI specifications, inspired by openapi-client-axios.
    Uses httpx for HTTP requests and supports both synchronous and asynchronous operations.
    
    Usage:
        api = OpenAPIClient(definition_url)
        
        # Synchronous usage
        with api.Client() as client:
            result = client.operation_name(param1=value)
            
        # Asynchronous usage
        async with api.AsyncClient() as client:
            result = await client.operation_name(param1=value)
    """

    def __init__(self, definition=None):
        """
        Initialize the OpenAPI client.

        Args:
            definition: URL or file path to the OpenAPI definition, or a dictionary containing the definition
        """
        self.definition_source = definition
        self.definition = {}
        self.base_url = ''
        self.source_url = None  # Store the source URL if loaded from a URL

    def Client(self, **kwargs):
        """
        Create a synchronous client instance that can be used as a context manager.
        
        Args:
            **kwargs: Additional arguments to pass to httpx.Client
            
        Returns:
            Client: A synchronous client
        """
        return Client(self, **kwargs)
        
    def AsyncClient(self, **kwargs):
        """
        Create an asynchronous client instance that can be used as a context manager.
        
        Args:
            **kwargs: Additional arguments to pass to httpx.AsyncClient
            
        Returns:
            AsyncClient: An asynchronous client
        """
        return AsyncClient(self, **kwargs)

    def _process_file_definition(self):
        """Process definition from a file source"""
        with open(self.definition_source, 'r') as f:
            content = f.read()
            if self.definition_source.endswith('.yaml') or self.definition_source.endswith('.yml'):
                self.definition = yaml.safe_load(content)
            else:
                self.definition = json.loads(content)

    def _process_definition_response(self, response):
        """Process HTTP response and extract OpenAPI definition"""
        content_type = response.headers.get('Content-Type', '')
        if 'yaml' in content_type or 'yml' in content_type:
            self.definition = yaml.safe_load(response.text)
        elif self.definition_source.endswith('.yaml') or self.definition_source.endswith('.yml'):
            self.definition = yaml.safe_load(response.text)
        else:
            self.definition = response.json()

    async def _load_definition_async(self):
        """Load the OpenAPI definition asynchronously"""
        if isinstance(self.definition_source, dict):
            self.definition = self.definition_source
            return

        if os.path.isfile(str(self.definition_source)):
            # Load from file
            self._process_file_definition()
            return

        # Assume it's a URL
        self.source_url = self.definition_source  # Store the source URL
        async with httpx.AsyncClient() as client:
            response = await client.get(self.definition_source)
            if response.status_code == 200:
                self._process_definition_response(response)
            else:
                raise Exception(f"Failed to load OpenAPI definition: {response.status_code}")

    def _load_definition_sync(self):
        """Load the OpenAPI definition synchronously"""
        if isinstance(self.definition_source, dict):
            self.definition = self.definition_source
            return

        if os.path.isfile(str(self.definition_source)):
            # Load from file
            self._process_file_definition()
            return

        # Assume it's a URL
        self.source_url = self.definition_source  # Store the source URL
        with httpx.Client() as client:
            response = client.get(self.definition_source)
            if response.status_code == 200:
                self._process_definition_response(response)
            else:
                raise Exception(f"Failed to load OpenAPI definition: {response.status_code}")

    def get_operations(self):
        """
        Extract all operations from the OpenAPI definition.

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

    def _create_client(self, client_instance, is_async=False):
        """
        Create a client with dynamically generated methods from the OpenAPI spec.
        
        Args:
            client_instance: The client instance (AsyncClient or Client)
            is_async: Whether to create async or sync methods
            
        Returns:
            DynamicClient: A client with methods for each operation in the spec
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

            # Create the appropriate method type (async or sync)
            methods_dict[operation_id] = self._create_operation_method(
                client_instance, path, operation.get('method'), operation, is_async
            )

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
            '_client': client_instance,  # Store reference to the client
        }
        
        # Create the dynamic client class
        DynamicClientClass = type(class_name, (DynamicClientBase,), attribute_dict)

        # Create an instance of this class
        client = DynamicClientClass()

        # Return the client
        return client

    def _prepare_request_params(self, path, operation, kwargs):
        """
        Prepare request parameters for an API operation.

        Args:
            path: The path template
            operation: Operation object
            kwargs: Keyword arguments passed to the operation

        Returns:
            tuple: (full_url, query_params, body, headers, remaining_kwargs)
        """
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

        # Handle headers
        headers = kwargs.pop('headers', {})

        # Handle request body
        body = kwargs.pop('data', None) or kwargs.pop('body', None)
        # json body
        if not body and len(kwargs) > 0 and operation.get('requestBody', {}).get('content', {}).get('application/json'):
            body = kwargs.copy()
            kwargs.clear()  # Clear the kwargs after using them as body

        return full_url, query_params, body, headers, kwargs

    def _process_response(self, response):
        """
        Process response and return a standardized format.

        Args:
            response: HTTP response

        Returns:
            dict: Formatted response object
        """
        if 'application/json' in response.headers.get('Content-Type', ''):
            result = response.json()
        else:
            result = response.text

        # Create response object similar to axios
        return {
            'data': result,
            'status': response.status_code,
            'headers': dict(response.headers),
            'config': {}  # Original config dict is no longer available here
        }

    def _create_operation_method(self, client_instance, path, method, operation, is_async=False):
        """
        Create an operation method (either async or sync) for the OpenAPI spec.
        
        Args:
            client_instance: The client instance
            path: The path template
            method: The HTTP method
            operation: The operation object
            is_async: Whether to create an async method
            
        Returns:
            function: A method that performs the operation
        """
        if is_async:
            async def operation_method(*args, **kwargs):
                # Prepare request parameters
                full_url, query_params, body, headers, remaining_kwargs = self._prepare_request_params(
                    path, operation, kwargs.copy()
                )

                # Make the async request
                response = await client_instance.session.request(
                    method,
                    full_url,
                    params=query_params, 
                    json=body, 
                    headers=headers,
                    **remaining_kwargs
                )

                # Process the response
                return self._process_response(response)
        else:
            def operation_method(*args, **kwargs):
                # Prepare request parameters
                full_url, query_params, body, headers, remaining_kwargs = self._prepare_request_params(
                    path, operation, kwargs.copy()
                )

                # Make the sync request
                response = client_instance.session.request(
                    method,
                    full_url,
                    params=query_params, 
                    json=body, 
                    headers=headers,
                    **remaining_kwargs
                )

                # Process the response
                return self._process_response(response)

        # Set method metadata
        operation_method.__name__ = operation.get('operationId', '')
        operation_method.__doc__ = operation.get('summary', '') + "\n\n" + operation.get('description', '')

        return operation_method

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