# TallyPrime Ledger Setup Guide

## Required Ledgers for Invoice Import

### Step 1: Create Sales Account Ledger

1. Open TallyPrime
2. Go to **Gateway of Tally > Accounts Info > Ledgers > Create**
3. Enter these details:
   - **Name**: Sales Account
   - **Under**: Sales Accounts (select from list)
   - **Maintain balances bill-by-bill**: No
4. Press **Enter** to save

### Step 2: Create GST Output Ledgers

#### For CGST:
1. Go to **Gateway of Tally > Accounts Info > Ledgers > Create**
2. Enter these details:
   - **Name**: OUTPUT CGST @ 9%
   - **Under**: Duties & Taxes
   - **Type of duty/tax**: Central Tax
   - **Tax rate**: 9%
3. Press **Enter** to save

#### For SGST:
1. Go to **Gateway of Tally > Accounts Info > Ledgers > Create**
2. Enter these details:
   - **Name**: OUTPUT SGST @ 9%
   - **Under**: Duties & Taxes
   - **Type of duty/tax**: State Tax
   - **Tax rate**: 9%
3. Press **Enter** to save

#### For IGST (if needed):
1. Go to **Gateway of Tally > Accounts Info > Ledgers > Create**
2. Enter these details:
   - **Name**: OUTPUT IGST @ 18%
   - **Under**: Duties & Taxes
   - **Type of duty/tax**: Integrated Tax
   - **Tax rate**: 18%
3. Press **Enter** to save

### Step 3: Create Party Ledgers (Customer Accounts)

For each unique customer in your invoices, create a ledger:

1. Go to **Gateway of Tally > Accounts Info > Ledgers > Create**
2. Enter these details:
   - **Name**: [Exact customer name from invoice]
   - **Under**: Sundry Debtors
   - **Maintain balances bill-by-bill**: Yes
   - **State**: [Customer's state for GST]
   - **GSTIN/UIN**: [Customer's GSTIN if available]
3. Press **Enter** to save

### Current Customer Names to Create:
- SAMSON MARITIME LIMITED.
- Global Electrical And Marine Services

### Step 4: Import XML File

1. Save the generated XML file to your computer
2. In TallyPrime, go to **Gateway of Tally > Import of Data > XML Data**
3. Browse and select your XML file
4. TallyPrime will show a preview - verify the entries
5. Click **Accept** to import

### Troubleshooting Common Issues:

#### "Ledger does not exist" Error:
- Create the missing ledger following steps above
- Ensure exact name match (case-sensitive)

#### "Voucher Date is missing" Error:
- This should be fixed in the latest code
- If still occurring, check the date format in your CSV

#### "Invalid GST Rate" Error:
- Ensure GST ledgers are created with correct tax rates
- Verify the tax type (Central/State/Integrated)

### Verification Steps:

After import:
1. Go to **Gateway of Tally > Display > Account Books > Ledger**
2. Select any party ledger to view imported transactions
3. Check if invoice amounts and GST calculations are correct
4. Generate GST reports to verify tax calculations

## Sample XML Structure (Reference):

```xml
<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>All Masters</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE>
          <VOUCHER REMOTEID="..." VCHKEY="..." VCHTYPE="Sales" ACTION="Create">
            <DATE>20250416</DATE>
            <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
            <VOUCHERNUMBER>INV001</VOUCHERNUMBER>
            <PARTYLEDGERNAME>Customer Name</PARTYLEDGERNAME>
            <REFERENCE>INV001</REFERENCE>
            <REFERENCEDATE>20250416</REFERENCEDATE>
            <NARRATION>Sales Invoice INV001 to Customer Name</NARRATION>
            
            <!-- Ledger Entries -->
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Customer Name</LEDGERNAME>
              <AMOUNT>11800.00</AMOUNT>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            </ALLLEDGERENTRIES.LIST>
            
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Sales Account</LEDGERNAME>
              <AMOUNT>-10000.00</AMOUNT>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            </ALLLEDGERENTRIES.LIST>
            
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>OUTPUT CGST @ 9%</LEDGERNAME>
              <AMOUNT>-900.00</AMOUNT>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            </ALLLEDGERENTRIES.LIST>
            
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>OUTPUT SGST @ 9%</LEDGERNAME>
              <AMOUNT>-900.00</AMOUNT>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>
```

## Support:

If you encounter any issues:
1. Check that all required ledgers are created exactly as specified
2. Verify the XML file is valid and contains proper data
3. Ensure TallyPrime is updated to the latest version
4. Check GST settings and compliance requirements
