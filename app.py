from flask import Flask, request, render_template
from azure.cosmos import CosmosClient
from azure.search.documents import SearchClient
from azure.search.documents.models import QueryType
from azure.core.credentials import AzureKeyCredential
import os

app = Flask(__name__)

# ENV VARS
COSMOS_URL = os.getenv("COSMOS_URL")
COSMOS_KEY = os.getenv("COSMOS_KEY")
DB_NAME = "docdb"
CONTAINER_NAME = "documents"

SEARCH_ENDPOINT = os.getenv("SEARCH_ENDPOINT")
SEARCH_KEY = os.getenv("SEARCH_KEY")
INDEX_NAME = "docs-index"

# Validate environment variables
required_vars = [COSMOS_URL, COSMOS_KEY, SEARCH_ENDPOINT, SEARCH_KEY]
if not all(required_vars):
    raise ValueError("Missing required environment variables")

# Init clients
try:
    cosmos_client = CosmosClient(COSMOS_URL, COSMOS_KEY)
    db = cosmos_client.get_database_client(DB_NAME)
    container = db.get_container_client(CONTAINER_NAME)

    search_client = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=INDEX_NAME,
        credential=AzureKeyCredential(SEARCH_KEY)
    )
except Exception as e:
    print(f"Error initializing clients: {e}")
    raise


@app.route('/', methods=['GET', 'POST'])
def home():
    result = ''
    error = ''

    if request.method == 'POST':
        query = request.form.get('query', '').strip()

        if not query:
            error = "Please enter a search query"
        else:
            try:
                search_results = search_client.search(
                    search_text=query,
                    query_type=QueryType.SEMANTIC
                )

                # Extract content from search results
                results_list = []
                for doc in search_results:
                    # Handle different possible field names
                    content = doc.get('content') or doc.get('text') or doc.get('body') or str(doc)
                    results_list.append(content)

                if results_list:
                    result = "\n\n".join(results_list)
                else:
                    result = "No results found for your query."

            except Exception as e:
                error = f"Search error: {str(e)}"

    return render_template("index.html", result=result, error=error)


if __name__ == '__main__':
    app.run(debug=True)