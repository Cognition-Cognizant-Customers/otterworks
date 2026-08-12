import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  FileUploadDropzone,
  type FileUploadDropzoneHandle,
} from "./file-upload-dropzone";

function makeFile(name: string, size = 4) {
  return new File([new Uint8Array(size)], name, { type: "text/plain" });
}

describe("FileUploadDropzone", () => {
  it("starts multiple uploads and reports progress and completion", async () => {
    const progressCallbacks: ((value: number) => void)[] = [];
    const resolvers: (() => void)[] = [];
    const uploadFile = vi.fn(
      (_file: File, options: { onProgress: (value: number) => void }) =>
        new Promise<void>((resolve) => {
          progressCallbacks.push(options.onProgress);
          resolvers.push(resolve);
        }),
    );
    const onUploadComplete = vi.fn();
    const ref = createRef<FileUploadDropzoneHandle>();
    render(<FileUploadDropzone ref={ref} uploadFile={uploadFile} onUploadComplete={onUploadComplete} />);

    act(() => ref.current?.addFiles([makeFile("one.txt"), makeFile("two.txt")]));
    expect(uploadFile).toHaveBeenCalledTimes(2);
    expect(screen.getByText("one.txt")).toBeInTheDocument();
    expect(screen.getByText("two.txt")).toBeInTheDocument();
    expect(screen.getAllByTitle("Cancel upload")).toHaveLength(2);

    act(() => progressCallbacks[0](45));
    expect(screen.getByText("45%")).toBeInTheDocument();
    act(() => {
      resolvers.forEach((resolve) => resolve());
    });
    await waitFor(() => expect(onUploadComplete).toHaveBeenCalledTimes(2));
    expect(screen.getByText(/Upload complete/)).toBeInTheDocument();
  });

  it("shows an upload error and retries the failed item", async () => {
    let attempt = 0;
    let rejectUpload!: (reason?: unknown) => void;
    let resolveUpload!: () => void;
    const uploadFile = vi.fn(
      () =>
        new Promise<void>((resolve, reject) => {
          attempt += 1;
          if (attempt === 1) rejectUpload = reject;
          else resolveUpload = resolve;
        }),
    );
    const ref = createRef<FileUploadDropzoneHandle>();
    render(<FileUploadDropzone ref={ref} uploadFile={uploadFile} />);
    act(() => ref.current?.addFiles([makeFile("retry.txt")]));
    act(() => rejectUpload(new Error("failed")));
    expect(await screen.findByText("Upload failed")).toBeInTheDocument();

    fireEvent.click(screen.getByTitle("Retry upload"));
    expect(uploadFile).toHaveBeenCalledTimes(2);
    act(() => resolveUpload());
    await waitFor(() => expect(screen.getByText(/Upload complete/)).toBeInTheDocument());
  });

  it("cancels an in-flight upload and removes it from the queue", async () => {
    let signal!: AbortSignal;
    const uploadFile = vi.fn(
      (_file: File, options: { signal: AbortSignal }) =>
        new Promise<void>((_resolve, reject) => {
          signal = options.signal;
          signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    const ref = createRef<FileUploadDropzoneHandle>();
    render(<FileUploadDropzone ref={ref} uploadFile={uploadFile} />);
    act(() => ref.current?.addFiles([makeFile("cancel.txt")]));
    fireEvent.click(screen.getByTitle("Cancel upload"));
    await waitFor(() => expect(screen.queryByText("cancel.txt")).not.toBeInTheDocument());
    expect(signal.aborted).toBe(true);
  });
});
