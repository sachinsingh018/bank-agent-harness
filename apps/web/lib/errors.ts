// Page level error text: what happened and what to do next.

import { API_WAKING_MESSAGE, ApiError } from "./api";

export function loadErrorText(err: unknown, what: string): string {
  if (err instanceof ApiError && err.status === 0) return API_WAKING_MESSAGE;
  if (err instanceof ApiError && err.status === 422) {
    return "That page link is no longer valid. Start again from the first page.";
  }
  if (err instanceof ApiError && err.status === 503) {
    return `The ${what} could not be loaded because the database is not available. Try again later.`;
  }
  if (err instanceof ApiError) {
    return `The API returned an error (${err.status}: ${err.detail}) while loading the ${what}. Try again later.`;
  }
  return `Something went wrong while loading the ${what}. Reload the page to try again.`;
}
