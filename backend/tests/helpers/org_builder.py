"""Helpers that other tests share, moved out of test_org_builder.py so they import no test file."""

DEPARTMENTS = {
    "departments": [
        {
            "id": "sales",
            "name": "Sales",
            "head": "sales-manager",
            "icon": "trending_up",
            "question": "What kind of sales?",
        },
        {"id": "finance", "name": "Finance", "head": "controller", "icon": "account_balance"},
        {"id": "engineering", "name": "Engineering", "head": "eng-lead", "icon": "code", "software_only": True},
    ]
}


def card(template, department="", **fields):
    return {
        "template": template,
        "name": fields.pop("name", template.replace("-", " ").title()),
        "summary": fields.pop("summary", "Does " + template + " work."),
        "department": department,
        **fields,
    }


CARDS = [
    card("sales-manager", "sales", lead=True, suggest="default", icon="groups", summary="Runs the pipeline review."),
    card("sdr", "sales", suggest="default", tags=["cold email", "outbound"], summary="Writes first-touch emails."),
    card("account-manager", "sales", suggest="common", tags=["renewals", "upsell"], summary="Keeps accounts growing."),
    card("partner-scout", "sales", suggest="niche", tags=["partners", "resellers"], summary="Finds resellers."),
    card(
        "deal-desk",
        "sales",
        suggest="niche",
        tags=["contracts", "pricing"],
        summary="Prices big deals.",
        recommend_when=["sells_to_businesses"],
    ),
    card("controller", "finance", lead=True, summary="Closes the books."),
    card("collections", pack="operations", summary="Chases late invoices."),  # no department: its pack decides
    card("assistant", required=True),  # built in: never offered
]
