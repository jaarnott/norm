"""The automated-task guidance must invert inside an existing task.

A user typing "also email me the results" into her task's conversation got a
SECOND draft task instead of a change to the one she was looking at: the prompt
only ever advertised `create_automated_task`, and `update_automated_task` needs
a task_id nothing supplied. These pin that contract.
"""

from app.agents.prompt_builder import automated_tasks_guidance

TASK = {
    "id": "5289bf02-6285-4a0d-b265-cca2a6f52a82",
    "title": "Reconcile invoices",
    "prompt": "Reconcile received invoices against statements",
    "status": "active",
    "schedule": "daily at 08:00",
}


class TestInsideATask:
    def test_directs_the_model_to_update_with_the_task_id(self):
        text = automated_tasks_guidance(True, TASK)
        assert "update_automated_task" in text
        assert TASK["id"] in text, "the model cannot update without the id"

    def test_forbids_creating_another_task(self):
        text = automated_tasks_guidance(True, TASK)
        assert "Do NOT call `create_automated_task`" in text
        # The generic "call create_automated_task" advice must not also appear —
        # that is what produced the duplicate draft.
        assert "Call `create_automated_task` with `intent`" not in text

    def test_tells_the_model_to_make_lasting_instructions_durable(self):
        # Conversation history is summarised as the thread grows, so an
        # instruction left only in chat silently stops applying.
        text = automated_tasks_guidance(True, TASK)
        assert "FUTURE runs" in text
        assert "summarised" in text

    def test_carries_the_task_state_the_model_needs(self):
        text = automated_tasks_guidance(True, TASK)
        for expected in (TASK["title"], TASK["status"], TASK["schedule"]):
            assert expected in text


class TestOutsideATask:
    def test_offers_creation_when_tools_are_available(self):
        text = automated_tasks_guidance(True, None)
        assert "Call `create_automated_task` with `intent`" in text
        assert "update_automated_task" not in text

    def test_silent_when_the_tools_are_not_bound(self):
        assert automated_tasks_guidance(False, None) == ""
