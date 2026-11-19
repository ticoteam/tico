# A new worker, and a new state

## A new contractor

1. Decision record done (`contractor-or-employee.md`).
2. Contract signed by both sides: scope, rate, payment terms, who owns the work, how it ends. The signature is the
   owner's or needs their OK.
3. The contractor enters their own Form W-9 details and payment details through the provider's onboarding. The bot
   never receives or types them.
4. Line in `knowledge/contractors.md`: name, set up on, W-9 "on file" with date, entity type from the W-9, who holds
   the contact, review date.
5. If they will work on the company's premises, on a customer's site, or with tools and vehicles: ask for a
   certificate of insurance and pass it to `manage-insurance`. An uninsured contractor's injuries can land on the
   company's own workers' compensation policy.
6. Access to systems follows the access rules for outsiders, not the employee defaults.

## A new employee

Do these in order. Steps 1 and 2 can take weeks, so start them the day the offer is accepted.

1. **Is the work state new?** Compare the person's work address with the states set up in the provider. If it is new,
   do "A new state" below first.
2. **Workers' compensation.** Tell `manage-insurance` the state, the job and the start date. Coverage is in place on
   day one.
3. **The source.** The signed offer: start date, pay, exempt or hourly, schedule. The exempt decision has two parts
   under federal law, a duties test and a salary floor (684 dollars a week federally as of 2026-11-18; several states
   set a higher one). Read the state's page. An employee who does not meet both is paid overtime.
4. **Self-onboarding in the provider.** The employee enters Form W-4, the state withholding form, bank details and
   their part of Form I-9.
5. **Form I-9.** A person at the company, or an authorised representative, examines the employee's documents and
   completes the employer's section. The form is kept on file, not sent anywhere. The time limit after the start date
   is short: read the USCIS I-9 page for it (it was not confirmed on 2026-11-18). The bot schedules it and checks it
   was done; it does not examine documents.
6. **New-hire report** to the state directory. Many providers file it; check the provider's list for that state. The
   SBA's guide gives 20 days as the usual limit; some states are shorter.
7. **Posters and notices** the state requires for a remote employee: read the state labour page.
8. **Benefits.** Tell whoever administers benefits the start date so the enrolment window is not missed.
9. **First run.** The first pay for any new person is checked line by line in the preview: tax withheld is not zero,
   the state is right, the part period is right.

## A new state

A state is new when an employee starts working from it, including someone who moves. The state where the person
works usually decides, not where the company is.

1. **Read the state's pages** for: employer withholding registration (revenue department), unemployment insurance
   registration (workforce agency), any state disability or paid-leave programme, and local payroll taxes in the
   person's city. Write each page and date in `knowledge/payroll.md`.
2. **Ask for the OK to register** (a registration is a legal commitment), then register. Some providers register for
   the company for a fee; that is a spend inside or outside budget like any other.
3. **Enter the account numbers and the unemployment rate** in the provider when they arrive. Until they are in, the
   state is a blocker on the run. If the first pay date will arrive before the numbers do, read the provider's and
   the state's guidance on paying while a registration is pending and put the choice to the approver.
4. **Tell the other skills.** `manage-insurance`: add the state to workers' compensation before the first day.
   `stay-on-top-of-taxes`: an employee in a state can create sales tax and income tax nexus there, and the state may
   need the company registered to do business with its secretary of state.
5. **Add the calendar lines**: the state's withholding and unemployment returns, with the provider as filer.
6. **Annual rate notice.** The state sends a new unemployment tax rate, usually once a year. Enter it in the
   provider before the first run it applies to, and save the notice.

When the last employee in a state leaves, close the state accounts once final returns are filed, and remove the
state from the workers' compensation policy at renewal.
