/**
 * API functions for file uploads
 */

import { getBackendBaseURL } from "../config";
import { fetchWithAuth, readErrorDetail } from "../http/fetch";

export interface UploadedFileInfo {
  filename: string;
  size: number;
  path: string;
  virtual_path: string;
  artifact_url: string;
  extension?: string;
  modified?: number;
  markdown_file?: string;
  markdown_path?: string;
  markdown_virtual_path?: string;
  markdown_artifact_url?: string;
}

export interface UploadResponse {
  success: boolean;
  files: UploadedFileInfo[];
  message: string;
}

export interface ListFilesResponse {
  files: UploadedFileInfo[];
  count: number;
}

/**
 * Upload files to a thread
 */
export async function uploadFiles(
  threadId: string,
  files: File[],
): Promise<UploadResponse> {
  const formData = new FormData();

  files.forEach((file) => {
    formData.append("files", file);
  });

  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/threads/${threadId}/uploads`,
    {
      method: "POST",
      body: formData,
    },
  );

  if (!response.ok) {
    throw new Error(await readErrorDetail(response, "Upload failed"));
  }

  return response.json();
}

/**
 * List all uploaded files for a thread
 */
export async function listUploadedFiles(
  threadId: string,
): Promise<ListFilesResponse> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/threads/${threadId}/uploads/list`,
  );

  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, "Failed to list uploaded files"),
    );
  }

  return response.json();
}

/**
 * Delete an uploaded file
 */
export async function deleteUploadedFile(
  threadId: string,
  filename: string,
): Promise<{ success: boolean; message: string }> {
  const response = await fetchWithAuth(
    `${getBackendBaseURL()}/api/threads/${threadId}/uploads/${filename}`,
    {
      method: "DELETE",
    },
  );

  if (!response.ok) {
    throw new Error(await readErrorDetail(response, "Failed to delete file"));
  }

  return response.json();
}
