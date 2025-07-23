import os
import uuid
from flask import Flask, request, render_template, flash
from werkzeug.utils import secure_filename
from azure.cosmos import CosmosClient
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.models import QueryType
from azure.search.documents.indexes.models import (
    SearchIndex, SearchField, SearchFieldDataType, SimpleField, SearchableField
)
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv
from PyPDF2 import PdfReader
import logging

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app setup
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-key-change-in-production')

# Create upload directory
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Azure settings
COSMOS_URL = os.getenv("COSMOS_URL")
COSMOS_KEY = os.getenv("COSMOS_KEY")
DB_NAME = "docdb"
CONTAINER_NAME = "documents"
SEARCH_ENDPOINT = os.getenv("SEARCH_ENDPOINT")
SEARCH_KEY = os.getenv("SEARCH_KEY")
INDEX_NAME = "cosmosdb-index"

# Validate environment variables
required_vars = [COSMOS_URL, COSMOS_KEY, SEARCH_ENDPOINT, SEARCH_KEY]
missing_vars = [var_name for var_name, var_value in
                zip(['COSMOS_URL', 'COSMOS_KEY', 'SEARCH_ENDPOINT', 'SEARCH_KEY'], required_vars)
                if not var_value]

if missing_vars:
    raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Initialize Azure clients
try:
    cosmos_client = CosmosClient(COSMOS_URL, COSMOS_KEY)
    db = cosmos_client.get_database_client(DB_NAME)
    container = db.get_container_client(CONTAINER_NAME)

    # Create both search client and index client
    search_client = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=INDEX_NAME,
        credential=AzureKeyCredential(SEARCH_KEY)
    )

    index_client = SearchIndexClient(
        endpoint=SEARCH_ENDPOINT,
        credential=AzureKeyCredential(SEARCH_KEY)
    )

    logger.info("Azure clients initialized successfully")
except Exception as e:
    logger.error(f"Error initializing Azure clients: {e}")
    raise


def create_search_index_if_not_exists():
    """Create the search index if it doesn't exist."""
    try:
        # Check if index exists
        index_client.get_index(INDEX_NAME)
        logger.info(f"Index '{INDEX_NAME}' already exists")
        return True
    except Exception:
        logger.info(f"Index '{INDEX_NAME}' not found, creating...")

        # Define the index schema
        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="userId", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="filename", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="content", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
            SimpleField(name="upload_timestamp", type=SearchFieldDataType.String, filterable=True, sortable=True)
        ]

        # Create the index
        index = SearchIndex(name=INDEX_NAME, fields=fields)

        try:
            index_client.create_index(index)
            logger.info(f"Index '{INDEX_NAME}' created successfully!")
            return True
        except Exception as create_error:
            logger.error(f"Error creating index: {create_error}")
            return False


def add_document_to_search_index(document):
    """Add document to search index."""
    try:
        search_client.upload_documents([document])
        logger.info(f"Document added to search index: {document['id']}")
        return True
    except Exception as e:
        logger.error(f"Error adding document to search index: {e}")
        return False


def allowed_file(filename):
    """Check if the uploaded file has an allowed extension."""
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text_from_file(file_path, filename):
    """Extract text content from uploaded file."""
    try:
        if filename.lower().endswith(".pdf"):
            reader = PdfReader(file_path)
            text = "\n".join([page.extract_text() or "" for page in reader.pages])
            return text.strip()
        elif filename.lower().endswith(('.txt', '.doc', '.docx')):
            # For now, treat .doc/.docx as text files
            # You may want to add proper docx parsing with python-docx
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read().strip()
        else:
            return ""
    except Exception as e:
        logger.error(f"Error extracting text from {filename}: {e}")
        return ""


@app.route('/', methods=['GET', 'POST'])
def index():
    result = ""
    error = ""

    if request.method == 'POST':
        try:
            # Handle search query
            if 'query' in request.form and request.form.get('query', '').strip():
                query = request.form['query'].strip()
                logger.info(f"Searching for: {query}")

                # Ensure search index exists
                if not create_search_index_if_not_exists():
                    error = "Search index could not be created. Please check your Azure Search configuration."
                else:
                    try:
                        search_results = search_client.search(
                            search_text=query,
                            query_type=QueryType.SEMANTIC,
                            semantic_configuration_name="cosmo-sem-k"
                        )

                        # Extract content from search results
                        results_list = []
                        for doc in search_results:
                            content = doc.get('content', '')
                            filename = doc.get('filename', 'Unknown')
                            if content and content.strip():
                                results_list.append(f"From '{filename}':\n{content.strip()}")

                        if results_list:
                            result = "\n\n" + "=" * 50 + "\n\n".join([""] + results_list)
                        else:
                            result = "No results found for your query."
                    except Exception as search_error:
                        error = f"Search error: {str(search_error)}"
                        logger.error(f"Search error: {search_error}")

            # Handle file upload
            elif 'document' in request.files:
                file = request.files['document']

                if file.filename == '':
                    error = "No file selected."
                elif not allowed_file(file.filename):
                    error = f"File type not allowed. Supported types: {', '.join(ALLOWED_EXTENSIONS)}"
                elif file:
                    try:
                        filename = secure_filename(file.filename)
                        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                        file.save(file_path)

                        # Extract text from file
                        text = extract_text_from_file(file_path, filename)

                        if not text:
                            error = f"Could not extract text from {filename}. Please check the file format."
                        else:
                            # Generate unique document ID
                            doc_id = str(uuid.uuid4())

                            # Store document in Cosmos DB
                            document = {
                                "id": doc_id,
                                "userId": "demo_user",
                                "filename": filename,
                                "content": text,
                                "upload_timestamp": str(uuid.uuid1().time)
                            }

                            container.upsert_item(document)

                            # Also add to search index
                            search_success = True
                            if create_search_index_if_not_exists():
                                search_success = add_document_to_search_index(document)

                            # Clean up uploaded file
                            try:
                                os.remove(file_path)
                            except OSError:
                                logger.warning(f"Could not remove temporary file: {file_path}")

                            if search_success:
                                result = f"Successfully uploaded and indexed '{filename}'. Document ID: {doc_id}"
                            else:
                                result = f"Successfully uploaded '{filename}' to database, but indexing failed. Document ID: {doc_id}"

                            logger.info(f"Document uploaded: {filename} (ID: {doc_id})")

                    except Exception as upload_error:
                        error = f"Error processing upload: {str(upload_error)}"
                        logger.error(f"Upload error: {upload_error}")

            else:
                error = "Please provide either a search query or select a file to upload."

        except Exception as e:
            error = f"An error occurred: {str(e)}"
            logger.error(f"Request processing error: {e}")

    return render_template("index.html", result=result, error=error)


if __name__ == '__main__':
    app.run(debug=True)