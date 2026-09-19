import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient } from "@aws-sdk/lib-dynamodb";

// AWS_ENDPOINT_URL points the SDK at LocalStack. When unset the client talks
// to real AWS and picks up credentials from the environment or the role the
// UI runs under.
const endpoint = process.env.AWS_ENDPOINT_URL;
const region = process.env.AWS_DEFAULT_REGION ?? process.env.AWS_REGION ?? "us-east-1";

export const TABLE_NAME = process.env.DYNAMODB_TABLE_NAME ?? "backstop-local";

export const STATUS_INDEX = "StatusIndex";
export const ALL_TICKETS_INDEX = "AllTicketsIndex";
export const ENTITY_DATE_INDEX = "EntityDateIndex";

declare global {
  var __backstopDoc: DynamoDBDocumentClient | undefined;
}

function createClient(): DynamoDBDocumentClient {
  const client = new DynamoDBClient({ region, ...(endpoint ? { endpoint } : {}) });
  return DynamoDBDocumentClient.from(client, {
    marshallOptions: { removeUndefinedValues: true },
  });
}

// Reuse the client across hot reloads in dev.
export const doc = global.__backstopDoc ?? createClient();
if (process.env.NODE_ENV !== "production") {
  global.__backstopDoc = doc;
}
