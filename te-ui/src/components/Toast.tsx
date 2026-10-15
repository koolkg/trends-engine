import { useEffect } from "react";

interface Props {
  message: string | null;
  onDone: () => void;
  ms?: number;
}

export function Toast({ message, onDone, ms = 2500 }: Props) {
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(onDone, ms);
    return () => clearTimeout(t);
  }, [message, onDone, ms]);
  return (
    <div className={`toast${message ? " show" : ""}`} role="status" aria-live="polite">
      {message}
    </div>
  );
}
