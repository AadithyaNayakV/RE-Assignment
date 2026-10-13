"""The service layer shared by the CLI's new commands and the local web app.

`core` reads what the engine wrote and calls the engine; `commands` builds the exact CLI
command for each action; `jobs` runs long actions in the background for the web app;
`uploads` saves a workbook safely; `parity` maps every web route to its CLI command.

Nothing here calculates a figure. It imports nothing outside the standard library, PyYAML
and the engine, so `finance_engine` keeps working without the optional web packages.
"""
