# Technical or security questionnaire

Triggered by a task handing over the technical or security part of an RFP or questionnaire. Budget 90
minutes for fifty questions. The outcome is an answer per question with its status and source, and a
list for named owners. Nothing is sent.

---

## 1. Sort

    tico task show <id>

Sort the questions: architecture, integrations and API, data handling, security controls, compliance and
certifications, availability and support. Note the deadline and format.

## 2. Answer from sources only

For each, `tico doc ask "<question>"` and check the documented answers the Account Executive keeps. An
answer under twelve months old is **reused**; one needing change is **adapted** with the new source; no
source means **owner**: the question goes to the named security or engineering owner, never improvised.
"Partially" answers say exactly what is and is not supported.

## 3. Write the return file

In the buyer's format, or a table: question, answer, status, source and date, owner. Counts at the top.

## 4. Hand back

Attach it to the task and hand it to the Account Executive, who merges it with the commercial half. Once an
owner confirms a new answer, record it for reuse with the owner and date. `tico task done <id> -m
"..." --version N`: counts by status, owners and the date each answer is needed.
