# -*- coding: utf-8 -*-
"""
任务管理器 - 后台任务调度与执行
负责管理和调度各种工具调用任务，支持异步执行和任务队列管理

在 main.py 中已集成，会自动启动
提交任务示例：
task_id = task_manager.submit_task(
    "工具名称",
    tool_function,
    arg1, arg2,
    priority=TaskPriority.HIGH,
    kwarg1=value1
)
"""
import asyncio
import logging
import time
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)

class TaskStatus(Enum):
    """任务状态枚举"""
    PENDING = "pending"      # 等待执行
    RUNNING = "running"      # 执行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 失败
    CANCELLED = "cancelled"  # 已取消

class TaskPriority(Enum):
    """任务优先级"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4

@dataclass
class Task:
    """任务数据结构"""
    task_id: str
    name: str
    func: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "priority": self.priority.name,
            "status": self.status.value,
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
            "started_at": datetime.fromtimestamp(self.started_at).isoformat() if self.started_at else None,
            "completed_at": datetime.fromtimestamp(self.completed_at).isoformat() if self.completed_at else None,
            "error": self.error
        }

class TaskManager:
    """任务管理器 - 单例模式"""
    
    _instance = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self._tasks: Dict[str, Task] = {}
            self._task_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
            self._workers: list = []
            self._max_workers = 5  # 最大并发工作线程数
            self._is_running = False
            self._shutdown_event = asyncio.Event()
            logger.info("任务管理器初始化完成")
    
    @property
    def is_running(self) -> bool:
        """获取运行状态"""
        return self._is_running
    
    async def start(self):
        """启动任务管理器"""
        if self._is_running:
            logger.warning("任务管理器已在运行中")
            return
        
        self._is_running = True
        self._shutdown_event.clear()
        
        # 启动工作线程
        for i in range(self._max_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        
        logger.info(f"任务管理器已启动，工作线程数: {self._max_workers}")
    
    async def stop(self):
        """停止任务管理器"""
        if not self._is_running:
            return
        
        logger.info("正在停止任务管理器...")
        self._is_running = False
        self._shutdown_event.set()
        
        # 等待所有工作线程完成
        for worker in self._workers:
            worker.cancel()
        
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        
        logger.info("任务管理器已停止")
    
    async def _worker(self, worker_id: int):
        """工作线程 - 从队列中获取并执行任务"""
        logger.info(f"工作线程 {worker_id} 已启动")
        
        while self._is_running:
            try:
                # 从优先队列获取任务（阻塞，带超时）
                try:
                    priority, task_id = await asyncio.wait_for(
                        self._task_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                task = self._tasks.get(task_id)
                if not task:
                    continue
                
                # 执行任务
                await self._execute_task(task, worker_id)
                
            except asyncio.CancelledError:
                logger.info(f"工作线程 {worker_id} 被取消")
                break
            except Exception as e:
                logger.error(f"工作线程 {worker_id} 异常: {e}")
        
        logger.info(f"工作线程 {worker_id} 已停止")
    
    async def _execute_task(self, task: Task, worker_id: int):
        """执行任务"""
        try:
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            logger.info(f"工作线程 {worker_id} 开始执行任务: {task.name} ({task.task_id})")
            
            # 执行任务函数
            if asyncio.iscoroutinefunction(task.func):
                result = await task.func(*task.args, **task.kwargs)
            else:
                result = task.func(*task.args, **task.kwargs)
            
            task.result = result
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            
            elapsed = task.completed_at - task.started_at
            logger.info(f"任务 {task.name} 完成，耗时: {elapsed:.2f}秒")
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
            logger.error(f"任务 {task.name} 执行失败: {e}")
    
    def submit_task(
        self,
        name: str,
        func: Callable,
        *args,
        priority: TaskPriority = TaskPriority.NORMAL,
        **kwargs
    ) -> str:
        """提交任务到队列"""
        task_id = str(uuid.uuid4())
        
        task = Task(
            task_id=task_id,
            name=name,
            func=func,
            args=args,
            kwargs=kwargs,
            priority=priority
        )
        
        self._tasks[task_id] = task
        
        # 添加到优先队列（优先级值越大越先执行，需要取负）
        asyncio.create_task(self._task_queue.put((-priority.value, task_id)))
        
        logger.info(f"任务已提交: {name} ({task_id}), 优先级: {priority.name}")
        return task_id
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        task = self._tasks.get(task_id)
        if task:
            return task.to_dict()
        return None
    
    def get_all_tasks(self) -> Dict[str, Dict[str, Any]]:
        """获取所有任务状态"""
        return {task_id: task.to_dict() for task_id, task in self._tasks.items()}
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.PENDING:
            task.status = TaskStatus.CANCELLED
            logger.info(f"任务已取消: {task.name} ({task_id})")
            return True
        return False
    
    def clear_completed_tasks(self):
        """清理已完成的任务记录"""
        completed = [
            task_id for task_id, task in self._tasks.items()
            if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]
        ]
        
        for task_id in completed:
            del self._tasks[task_id]
        
        logger.info(f"已清理 {len(completed)} 个已完成的任务")
    
    def get_statistics(self) -> Dict[str, int]:
        """获取任务统计信息"""
        stats = {
            "total": len(self._tasks),
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0
        }
        
        for task in self._tasks.values():
            stats[task.status.value] += 1
        
        return stats


# 创建全局单例
task_manager = TaskManager()


# ============= 工具调用示例函数 =============

async def example_tool_call(tool_name: str, params: dict) -> dict:
    """示例工具调用函数"""
    logger.info(f"调用工具: {tool_name}, 参数: {params}")
    await asyncio.sleep(2)  # 模拟工具执行
    return {"status": "success", "tool": tool_name, "result": "执行完成"}


async def crawl4ai_call(url: str, **kwargs) -> dict:
    """Crawl4AI 工具调用"""
    logger.info(f"Crawl4AI 开始爬取: {url}")
    await asyncio.sleep(3)  # 模拟爬取过程
    return {
        "url": url,
        "status": "success",
        "content": f"爬取内容示例 from {url}"
    }


def sync_tool_call(tool_name: str, params: dict) -> dict:
    """同步工具调用示例"""
    logger.info(f"同步调用工具: {tool_name}")
    time.sleep(1)
    return {"status": "success", "tool": tool_name}


# ============= 测试代码 =============

async def test_task_manager():
    """测试任务管理器"""
    logger.info("开始测试任务管理器...")
    
    # 启动任务管理器
    await task_manager.start()
    
    # 提交多个任务
    task1 = task_manager.submit_task(
        "测试工具1",
        example_tool_call,
        "tool1",
        {"param": "value1"},
        priority=TaskPriority.NORMAL
    )
    
    task2 = task_manager.submit_task(
        "Crawl4AI爬取",
        crawl4ai_call,
        "https://example.com",
        priority=TaskPriority.HIGH
    )
    
    task3 = task_manager.submit_task(
        "同步工具",
        sync_tool_call,
        "sync_tool",
        {"param": "value3"},
        priority=TaskPriority.LOW
    )
    
    # 等待任务完成
    await asyncio.sleep(5)
    
    # 打印统计信息
    stats = task_manager.get_statistics()
    logger.info(f"任务统计: {stats}")
    
    # 打印任务状态
    for task_id in [task1, task2, task3]:
        status = task_manager.get_task_status(task_id)
        logger.info(f"任务 {task_id}: {status}")
    
    # 停止任务管理器
    await task_manager.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_task_manager())