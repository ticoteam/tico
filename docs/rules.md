# Rules from a document

Counsel reads a contract, a policy or another document you add. It suggests the obligations that bind your team, each with the sentence from the document it came from. You accept the suggestions that are right. Counsel suggests only obligations that bind your team and that a bot or a person could break.

## Add a document

1. Open **Rules** and click the **External** tab.
2. Click **+ Add document**.
3. Under **Document**, pick a file from Files, or paste a public link. Counsel reads text, so an image, video or audio file cannot be read.
4. Under **From**, pick the source the document comes from. To add a new one, pick **New…**, type its **Name** and pick its kind: **Vendor**, **Customer**, **Government** or **Other**.
5. Optionally, type a **Description**.
6. Click **Read and suggest rules**.

The dialog says **Counsel is reading** the document and links to Counsel's task. Its suggestions wait on the **Suggested** tab.

## Accept the suggestions

1. Open **Rules** and click the **Suggested** tab.
2. Find the suggestion under its **From** source. Each row shows one rule.
3. If the row has no **Who**, pick who the rule applies to. **Add** stays off until **Who** is set.
4. Click **Add** to accept the rule, or **Skip** to set it aside.
5. To accept every ready suggestion from one source, click **Add all** above that source's rows. The button shows only when two or more are ready.

An accepted rule stays on the **External** tab, and it applies to the bots and people you named under **Who**. Open the rule to see its quote and the section it came from.

A rule's quote points to the version of the document that Counsel read. A later change to the document does not change that quote.

## If Counsel does not suggest rules

- **Counsel is off.** Only the owner can turn Counsel on or off.
- **Screening held** appears in the dialog. Tico's screening flagged the document's text, so Counsel does not read it and suggests nothing from it.
- **Long document.** The dialog says **Long document: Counsel reads only the start.** Counsel reads the first part of the text and suggests rules only from that part.
- **Counsel is already reading** the document means its read is still open. Counsel does not start a second read.

If you cannot see a rule's quote, you do not have access to its file. You still see the rule's sentence.

## Who can read a Counsel read

Counsel's read task is private to you and Counsel. A teammate who cannot read that task cannot read the document's text. Counsel has no credentials and sends nothing. Its suggestions wait for a person to accept or skip them.

## From the command line

Add a document with `hub rules read <file-id|url> --from <source>`, and add `--kind` for a new source. Then list the suggestions waiting for a person with `hub rules suggested`. A person accepts a suggestion with `hub rules accept <id> --version <n>`. A bot cannot accept one.
