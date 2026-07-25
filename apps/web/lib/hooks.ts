"use client";

import {
  useQuery,
  type QueryKey,
  type UseQueryOptions,
  type UseQueryResult,
} from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  CurrentUser,
  GraphNeighbors,
  GraphResponse,
} from "@/lib/types";

/** Shared query key for the authenticated user + active org context. */
export const CURRENT_USER_KEY: QueryKey = ["current-user"];

/**
 * Loads `GET /users/me` - the authenticated user, their organizations, the
 * active org and the caller's role within it. This is the single source of
 * truth for identity; {@link useAuth} in `auth-context` wraps it with actions.
 */
export function useCurrentUser(
  options?: Partial<UseQueryOptions<CurrentUser>>,
): UseQueryResult<CurrentUser> {
  return useQuery<CurrentUser>({
    queryKey: CURRENT_USER_KEY,
    queryFn: () => api.get<CurrentUser>("/users/me"),
    staleTime: 60_000,
    retry: false,
    ...options,
  });
}

type ApiParams = Parameters<typeof api.get>[1];

/**
 * Generic typed wrapper around `api.get` + TanStack Query. Keeps dashboard
 * pages terse:
 *
 * ```ts
 * const { data } = useApiQuery<Collection[]>(["collections"], "/collections");
 * ```
 */
export function useApiQuery<T>(
  key: QueryKey,
  path: string,
  params?: ApiParams,
  options?: Partial<UseQueryOptions<T>>,
): UseQueryResult<T> {
  return useQuery<T>({
    queryKey: key,
    queryFn: () => api.get<T>(path, params),
    ...options,
  });
}

/** Query params for {@link useGraph} (mirrors `GET /graph`). */
export interface GraphParams {
  collection_id?: string;
  limit?: number;
  min_similarity?: number;
  max_neighbors?: number;
}

/**
 * Loads the permission-scoped knowledge graph - `GET /graph`. Nodes are
 * documents the caller may view; edges connect two visible documents by
 * semantic similarity. All params are optional and echoed back by the API.
 */
export function useGraph(
  params: GraphParams = {},
  options?: Partial<UseQueryOptions<GraphResponse>>,
): UseQueryResult<GraphResponse> {
  return useApiQuery<GraphResponse>(
    ["graph", params],
    "/graph",
    { ...params },
    options,
  );
}

/**
 * Loads a single document's nearest neighbors - `GET
 * /graph/documents/{id}/neighbors`. Disabled until `documentId` is set; the
 * API returns 404 for a document the caller cannot view.
 */
export function useDocumentNeighbors(
  documentId: string | null | undefined,
  limit?: number,
  options?: Partial<UseQueryOptions<GraphNeighbors>>,
): UseQueryResult<GraphNeighbors> {
  return useApiQuery<GraphNeighbors>(
    ["graph-neighbors", documentId, limit],
    `/graph/documents/${documentId}/neighbors`,
    limit === undefined ? undefined : { limit },
    { enabled: !!documentId, ...options },
  );
}
