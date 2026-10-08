import { toast as sonner } from "sonner";

export const toast = {
  success(message: string) {
    sonner.success(message);
  },
  warning(message: string) {
    sonner.warning(message);
  },
  error(message: string, onRetry?: () => void) {
    sonner.error(
      message,
      onRetry ? { action: { label: "Retry", onClick: onRetry } } : undefined,
    );
  },
};
