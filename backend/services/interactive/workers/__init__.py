"""Interactive debate workers – event-driven processors for A2A and HITL.

These workers listen to Redis Streams and process events:
- A2AWorker: Calls external A2A agents when a2a_request events arrive
- HITLWorker: Manages human-in-the-loop interactions
- AgentWorker: Calls LLM agents when agent_speech is requested

They run as background tasks and append results back to the EventStore.
"""

from backend.services.interactive.workers.a2a_worker import A2AWorker
from backend.services.interactive.workers.agent_worker import AgentWorker
from backend.services.interactive.workers.hitl_worker import HITLWorker
from backend.services.interactive.workers.manager import WorkerManager

__all__ = ["A2AWorker", "HITLWorker", "AgentWorker", "WorkerManager"]
