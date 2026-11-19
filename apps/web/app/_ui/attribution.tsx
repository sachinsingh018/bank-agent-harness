// Credit for the synthetic transfers, shown on the results and about pages.

export function Attribution() {
  return (
    <p className="meta prose">
      Transfers come from the IBM synthetic{" "}
      <a href="https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml">
        Transactions for Anti Money Laundering
      </a>{" "}
      dataset (HI-Small), by Erik Altman, licensed{" "}
      <a href="https://cdla.dev/sharing-1-0/">CDLA Sharing 1.0</a>.
    </p>
  );
}
