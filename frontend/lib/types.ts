export type PaymentStatus =
  | "Awaiting approval"
  | "Reconciling"
  | "Confirmed"
  | "Budget stopped"
  | "Failed";

export type Payment = {
  id: string;
  intent: string;
  rail: "M-Pesa" | "Paystack" | "MTN MoMo";
  corridor: string;
  amount: string;
  amountUsd: number;
  recipient: string;
  status: PaymentStatus;
  age: string;
  risk: "Low" | "Moderate" | "High";
  reason?: string;
};

export type Rail = {
  name: string;
  market: string;
  status: "Operational" | "Degraded";
  uptime: string;
  latency: string;
  color: string;
  bars: number[];
};
