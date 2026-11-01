export type HealthResponse = {
  status: "ok";
  database: "ok";
};

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch("/api/v1/health");

  if (!response.ok) {
    throw new Error(`Health request failed with status ${response.status}`);
  }

  return (await response.json()) as HealthResponse;
}
