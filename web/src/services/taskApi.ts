import api from './api';
import type {
  Task,
  TaskListResponse,
  TaskCreateRequest,
  TaskStage,
  TaskDecomposeRequest,
  TaskDecomposeResponse,
  TaskBatchCreateRequest,
  TaskBatchCreateResponse,
} from '@/types/task';

export async function listTasks(params?: {
  status?: string;
  page?: number;
  page_size?: number;
  start_date?: string;
  end_date?: string;
  project_id?: string;
  title?: string;
}): Promise<TaskListResponse> {
  const { data } = await api.get<{ items: Task[]; total: number; page: number; page_size: number }>('/tasks', { params });
  return { tasks: data.items, total: data.total, page: data.page, page_size: data.page_size };
}

export async function createTask(req: TaskCreateRequest): Promise<Task> {
  const { data } = await api.post<Task>('/tasks', req);
  return data;
}

export async function getTask(id: string): Promise<Task> {
  const { data } = await api.get<Task>(`/tasks/${id}`);
  return data;
}

export async function getTaskStages(id: string): Promise<TaskStage[]> {
  const { data } = await api.get<TaskStage[]>(`/tasks/${id}/stages`);
  return data;
}

export async function cancelTask(id: string): Promise<void> {
  await api.post(`/tasks/${id}/cancel`);
}

export async function retryTask(id: string): Promise<void> {
  await api.post(`/tasks/${id}/retry`);
}

export async function decomposePrd(req: TaskDecomposeRequest): Promise<TaskDecomposeResponse> {
  const { data } = await api.post<TaskDecomposeResponse>('/tasks/decompose', req);
  return data;
}

export async function batchCreateTasks(req: TaskBatchCreateRequest): Promise<TaskBatchCreateResponse> {
  const { data } = await api.post<TaskBatchCreateResponse>('/tasks/batch', req);
  return data;
}
