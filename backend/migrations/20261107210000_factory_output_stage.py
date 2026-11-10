"""Retired (migrations.SUPERSEDED): it set factories.output_stage_id from the old engine's pipelines
(pipeline_setup.completion_stage), which the Pipelines v3 cut-over deleted. Its version stays recorded, so a database
that ran it does not run it again, and a new database records it without running anything."""


def up(conn):
    pass
