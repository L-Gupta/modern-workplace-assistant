#!/usr/bin/env python3
"""
Microsoft Foundry Agent Sample - Tutorial 1 (File Search variant): Modern Workplace Assistant

This is a variant of main.py for teams that don't have a live SharePoint site
connected to their Foundry project. Instead of the SharepointPreviewTool, it
grounds the agent on the local *.docx files in ./sharepoint-sample-data using
a Foundry vector store + FileSearchTool (retrieval-augmented search over
uploaded documents, no SharePoint/M365 connection required).

Everything else - agent structure, MCP integration, conversation handling -
matches main.py so the two are easy to compare.

Business Scenario:
An employee needs to implement Azure AD multi-factor authentication. They need:
1. Company security policy requirements (from the local policy documents)
2. Technical implementation steps (from Microsoft Learn via MCP)
3. Combined guidance showing how policy requirements map to technical implementation
"""

import os
import time
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    PromptAgentDefinition,
    FileSearchTool,
    MCPTool,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from openai.types.responses.response_input_param import (
    McpApprovalResponse,
)

load_dotenv()

# ============================================================================
# AUTHENTICATION SETUP
# ============================================================================
endpoint = "https://lakshyagupta-4816-resource.services.ai.azure.com/api/projects/lakshyagupta-4816"
model_name = "gpt-5-mini"

DATA_DIR = Path(__file__).parent / "sharepoint-sample-data"
VECTOR_STORE_NAME = "contoso-policy-docs"


def get_or_create_vector_store(openai_client):
    """
    Reuse an existing vector store by name, or build one from the local
    policy documents in ./sharepoint-sample-data.

    Returns:
        vector_store: The vector store object, or None if no documents were found.
    """

    for vector_store in openai_client.vector_stores.list():
        if vector_store.name == VECTOR_STORE_NAME:
            print(f"📁 Reusing existing vector store: {vector_store.id}")
            return vector_store

    doc_paths = sorted(
        p for p in DATA_DIR.glob("*.docx") if not p.name.startswith("~$")
    )
    if not doc_paths:
        print(f"⚠️  No .docx files found in {DATA_DIR}")
        return None

    print("📁 Creating vector store from local policy documents...")
    vector_store = openai_client.vector_stores.create(name=VECTOR_STORE_NAME)

    for doc_path in doc_paths:
        print(f"   Uploading {doc_path.name}...")
        with open(doc_path, "rb") as f:
            openai_client.vector_stores.files.upload_and_poll(
                vector_store_id=vector_store.id, file=f
            )

    print(f"✅ Vector store ready: {vector_store.id} ({len(doc_paths)} documents)")
    return vector_store


def create_workplace_assistant(project_client, openai_client):
    """
    Create a Modern Workplace Assistant using the Microsoft Foundry SDK.

    Grounds the agent on local policy documents via a vector store + FileSearchTool,
    with the same robust error handling / graceful degradation pattern as main.py.

    Returns:
        agent: The created agent object
    """

    print("🤖 Creating Modern Workplace Assistant...")

    # ========================================================================
    # FILE SEARCH (VECTOR STORE) SETUP
    # ========================================================================
    file_search_tool = None
    try:
        vector_store = get_or_create_vector_store(openai_client)
        if vector_store:
            file_search_tool = FileSearchTool(vector_store_ids=[vector_store.id])
            print("✅ File search tool configured successfully")
    except Exception as e:
        print(f"⚠️  File search tool unavailable: {e}")
        print("   Agent will operate without access to company policy documents")
        file_search_tool = None

    # ========================================================================
    # MICROSOFT LEARN MCP INTEGRATION SETUP
    # ========================================================================
    mcp_server_url = os.environ.get("MCP_SERVER_URL", "https://learn.microsoft.com/api/mcp")
    mcp_tool = None

    if mcp_server_url:
        print("📚 Configuring Microsoft Learn MCP integration...")
        print(f"   Server URL: {mcp_server_url}")

        try:
            mcp_tool = MCPTool(
                server_url=mcp_server_url,
                server_label="Microsoft_Learn_Documentation",
                require_approval="always",
            )
            print("✅ MCP tool configured successfully")
        except Exception as e:
            print(f"⚠️  MCP tool unavailable: {e}")
            print("   Agent will operate without Microsoft Learn access")
            mcp_tool = None
    else:
        print("📚 MCP integration skipped (MCP_SERVER_URL not set)")

    # ========================================================================
    # AGENT CREATION WITH DYNAMIC CAPABILITIES
    # ========================================================================
    if file_search_tool and mcp_tool:
        instructions = """You are a Modern Workplace Assistant for Contoso Corporation.

CAPABILITIES:
- Search Contoso's policy documents (remote work, security, data governance, collaboration standards)
- Access Microsoft Learn for current Azure and Microsoft 365 technical guidance
- Provide comprehensive solutions combining internal requirements with external implementation

RESPONSE STRATEGY:
- For policy questions: Search the company policy documents for Contoso-specific requirements and guidelines
- For technical questions: Use Microsoft Learn for current Azure/M365 documentation
- For implementation questions: Combine both sources to show how company policies map to technical implementation
- Always cite your sources and provide step-by-step guidance"""
    elif file_search_tool:
        instructions = """You are a Modern Workplace Assistant with access to Contoso Corporation's policy documents.

CAPABILITIES:
- Search Contoso's policy documents for company procedures and internal guidelines
- Provide detailed technical guidance based on your knowledge
- Combine company policies with general best practices"""
    elif mcp_tool:
        instructions = """You are a Technical Assistant with access to Microsoft Learn documentation.

CAPABILITIES:
- Access Microsoft Learn for current Azure and Microsoft 365 technical guidance
- Provide detailed implementation steps and best practices
- Explain Azure services, features, and configuration options"""
    else:
        instructions = """You are a Technical Assistant specializing in Azure and Microsoft 365 guidance.

CAPABILITIES:
- Provide detailed Azure and Microsoft 365 technical guidance
- Explain implementation steps and best practices
- Help with Azure AD, Conditional Access, MFA, and security configurations"""

    print(f"🛠️  Creating agent with model: {model_name}")

    tools = []
    if file_search_tool:
        tools.append(file_search_tool)
        print("   ✓ File search tool added")
    if mcp_tool:
        tools.append(mcp_tool)
        print("   ✓ MCP tool added")

    print(f"   Total tools: {len(tools)}")

    agent = project_client.agents.create_version(
        agent_name="modern-workplace-assistant-file-search",
        definition=PromptAgentDefinition(
            model=model_name,
            instructions=instructions,
            tools=tools if tools else None,
        ),
    )

    print(f"✅ Agent created successfully (name: {agent.name}, version: {agent.version})")
    return agent


def demonstrate_business_scenarios(agent, openai_client):
    """
    Demonstrate realistic business scenarios with the Microsoft Foundry SDK.
    """

    scenarios = [
        {
            "title": "📋 Company Policy Question (File Search Only)",
            "question": "What is Contoso's remote work policy?",
            "context": "Employee needs to understand company-specific remote work requirements",
            "learning_point": "File search tool retrieves internal company policies from the vector store",
        },
        {
            "title": "📚 Technical Documentation Question (MCP Only)",
            "question": (
                "According to Microsoft Learn, what is the correct way to implement "
                "Azure AD Conditional Access policies? Please include reference links "
                "to the official documentation."
            ),
            "context": "IT administrator needs authoritative Microsoft technical guidance",
            "learning_point": "MCP tool accesses Microsoft Learn for official documentation with links",
        },
        {
            "title": "🔄 Combined Implementation Question (File Search + MCP)",
            "question": (
                "Based on our company's remote work security policy, how should I configure "
                "my Azure environment to comply? Please include links to Microsoft "
                "documentation showing how to implement each requirement."
            ),
            "context": "Need to map company policy to technical implementation with official guidance",
            "learning_point": "Both tools work together: file search for policy + MCP for implementation docs",
        },
    ]

    print("\n" + "=" * 70)
    print("🏢 MODERN WORKPLACE ASSISTANT - BUSINESS SCENARIO DEMONSTRATION")
    print("=" * 70)
    print("This demonstration shows how AI agents solve real business problems")
    print("using the Microsoft Foundry SDK.")
    print("=" * 70)

    for i, scenario in enumerate(scenarios, 1):
        print(f"\n📊 SCENARIO {i}/3: {scenario['title']}")
        print("-" * 50)
        print(f"❓ QUESTION: {scenario['question']}")
        print(f"🎯 BUSINESS CONTEXT: {scenario['context']}")
        print(f"🎓 LEARNING POINT: {scenario['learning_point']}")
        print("-" * 50)

        print("🤖 AGENT RESPONSE:")
        response, status = create_agent_response(agent, scenario["question"], openai_client)

        if status == "completed" and response and len(response.strip()) > 10:
            print(f"✅ SUCCESS: {response[:300]}...")
            if len(response) > 300:
                print(f"   📏 Full response: {len(response)} characters")
        else:
            print(f"⚠️  RESPONSE: {response}")

        print(f"📈 STATUS: {status}")
        print("-" * 50)

        time.sleep(1)

    print("\n✅ DEMONSTRATION COMPLETED!")
    print("🎓 Key Learning Outcomes:")
    print("   • Microsoft Foundry SDK usage for enterprise AI")
    print("   • Retrieval-augmented grounding via vector store + FileSearchTool")
    print("   • Real business value through AI assistance")
    print("   • Foundation for governance and monitoring (Tutorials 2-3)")

    return True


def create_agent_response(agent, message, openai_client):
    """
    Create a response from the workplace agent using the Responses API,
    including MCP tool approval handling.
    """

    try:
        response = openai_client.responses.create(
            input=message,
            extra_body={
                "agent_reference": {"name": agent.name, "type": "agent_reference"}
            },
        )

        # MCP tool calls can chain (e.g. search -> fetch), and each one needs
        # its own approval, so keep approving until the model stops asking.
        for _ in range(12):
            approval_list = [
                McpApprovalResponse(
                    type="mcp_approval_response",
                    approve=True,
                    approval_request_id=item.id,
                )
                for item in response.output
                if item.type == "mcp_approval_request" and item.id
            ]

            if not approval_list:
                break

            response = openai_client.responses.create(
                input=approval_list,
                previous_response_id=response.id,
                extra_body={
                    "agent_reference": {"name": agent.name, "type": "agent_reference"}
                },
            )

        return response.output_text, "completed"

    except Exception as e:
        return f"Error in conversation: {str(e)}", "failed"


def interactive_mode(agent, openai_client):
    """Interactive mode for testing the workplace agent."""

    print("\n" + "=" * 60)
    print("💬 INTERACTIVE MODE - Test Your Workplace Agent!")
    print("=" * 60)
    print("Ask questions about Azure, M365, security, and technical implementation.")
    print("Type 'quit' to exit.")
    print("-" * 60)

    while True:
        try:
            question = input("\n❓ Your question: ").strip()

            if question.lower() in ["quit", "exit", "bye"]:
                break

            if not question:
                print("💡 Please ask a question about Azure or M365 technical implementation.")
                continue

            print("\n🤖 Workplace Agent: ", end="", flush=True)
            response, status = create_agent_response(agent, question, openai_client)
            print(response)

            if status != "completed":
                print(f"\n⚠️  Response status: {status}")

            print("-" * 60)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            print("-" * 60)

    print("\n👋 Thank you for testing the Modern Workplace Agent!")


def main():
    """Main execution flow demonstrating the complete sample."""

    print("🚀 Foundry - Modern Workplace Assistant (File Search variant)")
    print("Tutorial 1: Building Enterprise Agents with Microsoft Foundry SDK")
    print("=" * 70)

    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential) as project_client,
        project_client.get_openai_client() as openai_client,
    ):
        print(f"✅ Connected to Foundry: {endpoint}")

        try:
            agent = create_workplace_assistant(project_client, openai_client)
            demonstrate_business_scenarios(agent, openai_client)

            print("\n🎯 Try interactive mode? (y/n): ", end="")
            try:
                if input().lower().startswith("y"):
                    interactive_mode(agent, openai_client)
            except EOFError:
                print("n")

            print("\n🎉 Sample completed successfully!")
            print("📚 This foundation supports Tutorial 2 (Governance) and Tutorial 3 (Production)")
            print("🔗 Next: Add evaluation metrics, monitoring, and production deployment")

        except Exception as e:
            print(f"\n❌ Error: {e}")
            print("Please check your .env configuration and ensure:")
            print("  - FOUNDRY_PROJECT_ENDPOINT is correct")
            print("  - FOUNDRY_MODEL_NAME is deployed")
            print("  - Azure credentials are configured (az login)")


if __name__ == "__main__":
    main()
