/** The message of something thrown, for showing to the user. */
export const errorText = (error: unknown): string => (error instanceof Error ? error.message : String(error));
