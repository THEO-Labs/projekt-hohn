// Shared "is any PDF currently being uploaded?" gate.
// Used so that all upload buttons (across every company on the portfolio page)
// disable themselves while one file transfer is in progress — prevents the user
// from saturating bandwidth or hitting the FastAPI worker with 5 concurrent
// multi-megabyte multipart uploads. The Claude extraction is queued separately
// on the server, but the file transfer itself is a real per-connection cost.
import { useSyncExternalStore } from "react";

type Listener = () => void;

let active = 0;
const listeners = new Set<Listener>();

const notify = () => listeners.forEach((fn) => fn());

export const uploadGate = {
  start(): void {
    active += 1;
    notify();
  },
  end(): void {
    active = Math.max(0, active - 1);
    notify();
  },
  isActive(): boolean {
    return active > 0;
  },
  subscribe(fn: Listener): () => void {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  },
};

export function useUploadActive(): boolean {
  return useSyncExternalStore(uploadGate.subscribe, uploadGate.isActive, uploadGate.isActive);
}
