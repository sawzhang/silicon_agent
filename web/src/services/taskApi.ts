import api from './api';
import type { Task, TaskListResponse, TaskCreateRequest, TaskStage } from '@/types/task';

export async function listTasks(params?: {
  status?: string;
  page?: number;
  page_size?: number;
  start_date?: string;
  end_date?: string;
}): Promise<TaskListResponse> {
  const { data } = await api.get<TaskListResponse>('/tasks', { params });
  return data;
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
