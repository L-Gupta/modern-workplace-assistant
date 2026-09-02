#!/usr/bin/env python3
"""
Microsoft Foundry Agent Sample - Tutorial 1 (Multi-agent variant): Modern Workplace Assistant

This is a multi-agent variant of main_file_search.py. Instead of one agent with
two tools bolted on, this splits the work across three specialist agents plus
an orchestrator that routes questions to the right specialist(s) and merges
their answers:

- hr-policy-agent          FileSearchTool over remote work / expense / onboarding docs
- it-security-policy-agent FileSearchTool over security / governance / collaboration /
                           acceptable-use docs
- technical-docs-agent     MCPTool over Microsoft Learn
- workplace-orchestrator   No tools of its own - a FunctionTool per specialist, whose
                           local handler calls that specialist agent and returns its
                           answer as the function result

The azure-ai-projects v2 SDK has no built-in "call another agent as a tool"
primitive (the classic v1 connected_agent tool isn't carried over - see
migration/v1_to_v2_migration.py), so the orchestrator -> specialist call is done
by hand: a FunctionTool's local handler is just a call to
openai_client.responses.create(agent_reference=<specialist>).
"""

import json
import os
import time
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    PromptAgentDefinition,
    FileSearchTool,
    FunctionTool,
    MCPTool,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from openai.types.responses.response_input_param import (
    McpApprovalResponse,
)

load_dotenv()

endpoint = "https://lakshyagupta-4816-resource.services.ai.azure.com/api/projects/lakshyagupta-4816"
model_name = "gpt-5-mini"

DATA_DIR = Path(__file__).parent / "sharepoint-sample-data"

HR_DOCS = [
    "remote-work-policy.docx",
    "expense-and-travel-policy.docx",
    "employee-onboarding-guide.docx",
]
IT_SECURITY_DOCS = [
    "security-guidelines.docx",
    "data-governance-policy.docx",
    "collaboration-standards.docx",
    "acceptable-use-policy.docx",
]


def get_or_create_vector_store(openai_client, name: str, filenames: list[str]):
    """
    Reuse an existing vector store by name, or build one from the given
    filenames in ./sharepoint-sample-data.

    Returns:
        vector_store: The vector store object, or None if no documents were found.
    """

    for vector_store in openai_client.vector_stores.list():
        if vector_store.name == name:
            print(f"📁 Reusing existing vector store: {name} ({vector_store.id})")
            return vector_store

    doc_paths = [DATA_DIR / f for f in filenames if (DATA_DIR / f).exists()]
    if not doc_paths:
        print(f"⚠️  None of the expected documents were found for '{name}'")
        return None

    print(f"📁 Creating vector store '{name}' from local policy documents...")
    vector_store = openai_client.vector_stores.create(name=name)

    for doc_path in doc_paths:
        print(f"   Uploading {doc_path.name}...")
        with open(doc_path, "rb") as f:
            openai_client.vector_stores.files.upload_and_poll(
                vector_store_id=vector_store.id, file=f
            )

    print(f"✅ Vector store ready: {name} ({vector_store.id}, {len(doc_paths)} documents)")
    return vector_store


def create_specialist_agents(project_client, openai_client):
    """
    Create the three specialist agents: HR policy, IT/security policy, and
    technical documentation (Microsoft Learn via MCP).

    Returns:
        dict: {"hr": agent, "it_security": agent, "technical": agent} - any
        entry may be None if that specialist's tool could not be configured.
    """

    print("🤖 Creating specialist agents...")

    agents = {"hr": None, "it_security": None, "technical": None}

    # HR policy specialist
    try:
        hr_vector_store = get_or_create_vector_store(
            openai_client, "contoso-hr-policy-docs", HR_DOCS
        )
        if hr_vector_store:
            agents["hr"] = project_client.agents.create_version(
                agent_name="hr-policy-agent",
                definition=PromptAgentDefinition(
                    model=model_name,
                    instructions=(
                        "You are Contoso's HR Policy specialist. Search the HR policy "
                        "documents (remote work, expense and travel, onboarding) to answer "
                        "questions about Contoso's HR policies and procedures. Cite specifics "
                        "from the documents."
                    ),
                    tools=[FileSearchTool(vector_store_ids=[hr_vector_store.id])],
                ),
            )
            print(f"✅ hr-policy-agent created (version: {agents['hr'].version})")
    except Exception as e:
        print(f"⚠️  hr-policy-agent unavailable: {e}")

    # IT/security policy specialist
    try:
        it_vector_store = get_or_create_vector_store(
            openai_client, "contoso-it-security-docs", IT_SECURITY_DOCS
        )
        if it_vector_store:
            agents["it_security"] = project_client.agents.create_version(
                agent_name="it-security-policy-agent",
                definition=PromptAgentDefinition(
                    model=model_name,
                    instructions=(
                        "You are Contoso's IT/Security Policy specialist. Search the security, "
                        "data governance, collaboration standards, and acceptable use documents "
                        "to answer questions about Contoso's IT and security policies. Cite "
                        "specifics from the documents."
                    ),
                    tools=[FileSearchTool(vector_store_ids=[it_vector_store.id])],
                ),
            )
            print(f"✅ it-security-policy-agent created (version: {agents['it_security'].version})")
    except Exception as e:
        print(f"⚠️  it-security-policy-agent unavailable: {e}")

    # Technical documentation specialist (Microsoft Learn via MCP)
    try:
        mcp_server_url = os.environ.get("MCP_SERVER_URL", "https://learn.microsoft.com/api/mcp")
        agents["technical"] = project_client.agents.create_version(
            agent_name="technical-docs-agent",
            definition=PromptAgentDefinition(
                model=model_name,
                instructions=(
                    "You are a Technical Documentation specialist with access to Microsoft "
                    "Learn. Provide current Azure and Microsoft 365 technical guidance, "
                    "including reference links to official documentation."
                ),
                tools=[
                    MCPTool(
                        server_url=mcp_server_url,
                        server_label="Microsoft_Learn_Documentation",
                        require_approval="always",
                    )
                ],
            ),
        )
        print(f"✅ technical-docs-agent created (version: {agents['technical'].version})")
    except Exception as e:
        print(f"⚠️  technical-docs-agent unavailable: {e}")

    return agents


def create_orchestrator_agent(project_client, specialists):
    """
    Create the orchestrator agent. It has no domain tools of its own - only a
    FunctionTool per available specialist, so it can route questions to them.

    Returns:
        agent: The orchestrator agent object.
    """

    tools = []
    routing_lines = []

    if specialists["hr"]:
        tools.append(
            FunctionTool(
                type="function",
                strict=True,
                name="ask_hr_policy_agent",
                description=(
                    "Ask the HR Policy specialist about Contoso's remote work, "
                    "expense/travel, or employee onboarding policies."
                ),
                parameters={
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                    "additionalProperties": False,
                },
            )
        )
        routing_lines.append("- HR questions (remote work, expenses/travel, onboarding): call ask_hr_policy_agent")

    if specialists["it_security"]:
        tools.append(
            FunctionTool(
                type="function",
                strict=True,
                name="ask_it_security_policy_agent",
                description=(
                    "Ask the IT/Security Policy specialist about Contoso's security "
                    "guidelines, data governance, collaboration standards, or acceptable "
                    "use policy."
                ),
                parameters={
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                    "additionalProperties": False,
                },
            )
        )
        routing_lines.append(
            "- IT/security questions (security guidelines, data governance, collaboration "
            "standards, acceptable use): call ask_it_security_policy_agent"
        )

    if specialists["technical"]:
        tools.append(
            FunctionTool(
                type="function",
                strict=True,
                name="ask_technical_docs_agent",
                description=(
                    "Ask the Technical Documentation specialist for official Microsoft "
                    "Learn / Azure guidance, with reference links."
                ),
                parameters={
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                    "additionalProperties": False,
                },
            )
        )
        routing_lines.append("- Technical/Azure/M365 questions: call ask_technical_docs_agent")

    instructions = (
        "You are the Modern Workplace Orchestrator for Contoso Corporation. You have no "
        "knowledge of your own about Contoso policies or Microsoft technical documentation "
        "- you MUST call the appropriate specialist tool(s) to answer.\n\n"
        "ROUTING:\n" + "\n".join(routing_lines) + "\n\n"
        "For questions that span multiple domains (e.g. a policy question that also needs "
        "technical implementation guidance), call ALL relevant specialists and then "
        "synthesize a single combined answer, citing which specialist(s) contributed. "
        "Always answer using the specialists' responses - never answer from your own "
        "general knowledge."
    )

    print(f"🛠️  Creating orchestrator with {len(tools)} specialist tool(s)")

    agent = project_client.agents.create_version(
        agent_name="workplace-orchestrator",
        definition=PromptAgentDefinition(
            model=model_name,
            instructions=instructions,
            tools=tools,
        ),
    )

    print(f"✅ workplace-orchestrator created (version: {agent.version})")
    return agent


def create_agent_response(agent, message, openai_client):
    """
    Create a response from a single agent using the Responses API, including
    MCP tool approval handling (an agent may need several approval rounds if
    its tool calls chain, e.g. a Microsoft Learn search followed by a fetch).
    """

    try:
        response = openai_client.responses.create(
            input=message,
            extra_body={
                "agent_reference": {"name": agent.name, "type": "agent_reference"}
            },
        )

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


def create_orchestrator_response(orchestrator, specialists, message, openai_client):
    """
    Create a response from the orchestrator agent, dispatching any
    function_call items to the corresponding specialist agent and feeding
    the specialist's answer back as the function's output. Loops in case the
    orchestrator calls more than one specialist, or calls them sequentially.
    """

    dispatch = {
        "ask_hr_policy_agent": specialists["hr"],
        "ask_it_security_policy_agent": specialists["it_security"],
        "ask_technical_docs_agent": specialists["technical"],
    }

    try:
        response = openai_client.responses.create(
            input=message,
            extra_body={
                "agent_reference": {"name": orchestrator.name, "type": "agent_reference"}
            },
        )

        for _ in range(6):
            function_calls = [item for item in response.output if item.type == "function_call"]
            if not function_calls:
                break

            follow_up_input = []
            for fc in function_calls:
                specialist = dispatch.get(fc.name)
                if specialist is None:
                    output = f"Error: no specialist available for '{fc.name}'"
                else:
                    question = json.loads(fc.arguments)["question"]
                    print(f"   🔀 Routing to {specialist.name}: {question[:80]}...")
                    output, _status = create_agent_response(specialist, question, openai_client)
                follow_up_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": fc.call_id,
                        "output": output,
                    }
                )

            response = openai_client.responses.create(
                input=follow_up_input,
                previous_response_id=response.id,
                extra_body={
                    "agent_reference": {"name": orchestrator.name, "type": "agent_reference"}
                },
            )

        return response.output_text, "completed"

    except Exception as e:
        return f"Error in conversation: {str(e)}", "failed"


def demonstrate_business_scenarios(orchestrator, specialists, openai_client):
    """Demonstrate realistic business scenarios routed through the orchestrator."""

    scenarios = [
        {
            "title": "🧑‍💼 HR Policy Question (HR specialist only)",
            "question": "What is Contoso's expense and travel reimbursement policy?",
            "learning_point": "Orchestrator routes to hr-policy-agent only",
        },
        {
            "title": "🔒 IT/Security Policy Question (IT/Security specialist only)",
            "question": "What does Contoso's acceptable use policy say about BYOD?",
            "learning_point": "Orchestrator routes to it-security-policy-agent only",
        },
        {
            "title": "📚 Technical Documentation Question (Technical specialist only)",
            "question": (
                "According to Microsoft Learn, what is the correct way to implement "
                "Azure AD Conditional Access policies? Please include reference links."
            ),
            "learning_point": "Orchestrator routes to technical-docs-agent only",
        },
        {
            "title": "🔄 Hybrid Question (HR + Technical specialists)",
            "question": (
                "Based on Contoso's employee onboarding guide, what Azure/Entra steps "
                "does IT need to complete for a new employee? Include Microsoft Learn "
                "links for each step."
            ),
            "learning_point": "Orchestrator calls hr-policy-agent and technical-docs-agent, then merges",
        },
    ]

    print("\n" + "=" * 70)
    print("🏢 MODERN WORKPLACE ORCHESTRATOR - MULTI-AGENT DEMONSTRATION")
    print("=" * 70)

    for i, scenario in enumerate(scenarios, 1):
        print(f"\n📊 SCENARIO {i}/{len(scenarios)}: {scenario['title']}")
        print("-" * 50)
        print(f"❓ QUESTION: {scenario['question']}")
        print(f"🎓 LEARNING POINT: {scenario['learning_point']}")
        print("-" * 50)

        print("🤖 ORCHESTRATOR RESPONSE:")
        response, status = create_orchestrator_response(
            orchestrator, specialists, scenario["question"], openai_client
        )

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
    print("   • Multi-agent architecture: orchestrator + specialist agents")
    print("   • Routing via FunctionTool, dispatched locally to specialist agents")
    print("   • Hybrid questions answered by combining multiple specialists")

    return True


def interactive_mode(orchestrator, specialists, openai_client):
    """Interactive mode for testing the multi-agent workplace assistant."""

    print("\n" + "=" * 60)
    print("💬 INTERACTIVE MODE - Test Your Multi-Agent Workplace Assistant!")
    print("=" * 60)
    print("Ask questions spanning HR, IT/security, or Azure/M365 technical topics.")
    print("Type 'quit' to exit.")
    print("-" * 60)

    while True:
        try:
            question = input("\n❓ Your question: ").strip()

            if question.lower() in ["quit", "exit", "bye"]:
                break

            if not question:
                print("💡 Please ask a question.")
                continue

            print("\n🤖 Orchestrator: ", end="", flush=True)
            response, status = create_orchestrator_response(
                orchestrator, specialists, question, openai_client
            )
            print(response)

            if status != "completed":
                print(f"\n⚠️  Response status: {status}")

            print("-" * 60)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            print("-" * 60)

    print("\n👋 Thank you for testing the Multi-Agent Workplace Assistant!")


def main():
    """Main execution flow demonstrating the complete multi-agent sample."""

    print("🚀 Foundry - Modern Workplace Assistant (Multi-agent variant)")
    print("Tutorial 1: Building Enterprise Agents with Microsoft Foundry SDK")
    print("=" * 70)

    with (
        DefaultAzureCredential() as credential,
        AIProjectClient(endpoint=endpoint, credential=credential) as project_client,
        project_client.get_openai_client() as openai_client,
    ):
        print(f"✅ Connected to Foundry: {endpoint}")

        try:
            specialists = create_specialist_agents(project_client, openai_client)
            orchestrator = create_orchestrator_agent(project_client, specialists)

            demonstrate_business_scenarios(orchestrator, specialists, openai_client)

            print("\n🎯 Try interactive mode? (y/n): ", end="")
            try:
                if input().lower().startswith("y"):
                    interactive_mode(orchestrator, specialists, openai_client)
            except EOFError:
                print("n")

            print("\n🎉 Sample completed successfully!")

        except Exception as e:
            print(f"\n❌ Error: {e}")
            print("Please check your endpoint/model configuration and Azure credentials.")


if __name__ == "__main__":
    main()
