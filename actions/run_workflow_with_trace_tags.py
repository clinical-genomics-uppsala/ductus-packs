from st2common.runners.base_action import Action
from st2client.client import Client
from st2client.models import LiveAction

class RunWorkflowWithTraceTag(Action):
    def run(self, workflow_ref, parameters, trace_tags):
        client = Client()
        execution = LiveAction(
            action=workflow_ref,
            parameters=parameters,
            context={'trace_context': {'trace_tag': ", ".join(trace_tags)}}
        )
        result = client.liveactions.create(execution)
        return result