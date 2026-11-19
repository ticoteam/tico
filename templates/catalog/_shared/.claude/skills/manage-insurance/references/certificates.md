# Certificates of insurance

A certificate (in the US usually the ACORD 25 form) is a snapshot issued by the broker or carrier. It says on its
face that it is issued for information only and confers no rights on the holder. It cannot add or change coverage.
Being an **additional insured** on someone's policy takes an endorsement to that policy; a ticked box on a
certificate is not enough. (ACORD 25 wording as quoted by law-firm and agents' association articles read
2026-11-18; secondary.)

The log lives in `knowledge/certificates.md`, with two tables: Out and In.

## Going out: someone asks the company for a certificate

1. **Read the requirement in the signed contract or lease**, not in the email asking. Quote the clause. Note:
   - Coverages and limits required.
   - The holder's exact legal name and address.
   - Wording asked for: additional insured; primary and non-contributory; waiver of subrogation; notice of
     cancellation. Each of these needs the policy or an endorsement to actually say it.
   - How long the cover must be kept, including after the contract ends.
2. **Compare with the register.** For each item: met (policy, limit, endorsement), or not met.
3. **If everything is met**, ask the broker in one message for the certificate and a copy of each endorsement relied
   on. Give the holder's name and address and the contract clause.
4. **If something is not met**, do not send a partial certificate and hope. It is a gap: get the price of the
   endorsement or limit, and put the choice to the owner (buy it, or go back to the customer through the contract's
   owner to change the clause). Many requirements are negotiable before signature and not after.
5. **Check what comes back**: insured name, policy numbers, dates and limits equal the register; the holder is
   named exactly; the endorsements are attached.
6. **Send it** from the task, and log: holder, contract, what was required, policies shown, endorsements, date
   sent, expiry (the earliest policy expiry on it).
7. **At each renewal**, reissue to every holder on the Out table whose contract is still live.

Before a contract is signed, the insurance clause is checked against the register by whoever reviews the contract
(`review-a-contract`). A clause the company cannot meet is found then, not when the certificate is asked for.

## Coming in: the company asks others for a certificate

Who must give one, by default:

- Any contractor or vendor doing physical work, working on the company's or a customer's premises, or driving for
  the company: general liability, workers' compensation, and auto where relevant.
- Any warehouse, 3PL or carrier holding the company's goods: warehouse legal liability or cargo cover, and the
  limit against the value of the goods held.
- Any manufacturer or supplier of products the company sells under its name: product liability, with the company
  named as additional insured by endorsement.
- A landlord, where the lease says the landlord insures the building.
- Remote software contractors: professional liability and cyber where they touch customer data or production
  systems; the owner may waive this for small engagements, in writing.

Steps:

1. Put the requirement in the contract before it is signed: coverages, limits, additional insured, and that a
   certificate is given before work starts and at each renewal.
2. Ask for the certificate and the endorsement. Read them: the insured's name equals the contracting party; dates
   cover the work; limits meet the contract; the company is named where required.
3. Log it in the In table with its expiry.
4. Chase 30 days before expiry (`chase-a-quiet-vendor`). A lapsed certificate for physical work means the work
   pauses; tell the teammate who manages the vendor.
5. Keep certificates for the life of the relationship and afterwards with the contract.

## The log

```
Out: | Holder | Contract and clause | Required | Policies and endorsements shown | Sent | Expires | Reissue due |
In:  | Vendor | Contract | Required | Shown | Additional insured endorsement (yes / no) | Received | Expires | Chased |
```

## Never

- Change anything on a certificate, fill one in, or reuse an old one with a new date. Only the broker or carrier
  issues it. Altering one is fraud.
- Send a certificate showing a policy that has been cancelled or is not yet bound.
- Agree in a contract to insurance wording nobody has checked against the policy.
- Accept "we're insured" in an email in place of the document.
