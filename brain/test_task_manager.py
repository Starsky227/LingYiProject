import asyncio
import logging
from task_manager import task_manager, TaskPriority

logging.basicConfig(level=logging.INFO)

async def main():
    print("启动任务管理器测试...")
    
    # 启动管理器
    await task_manager.start()
    
    # 提交多个测试任务
    async def async_task(name, delay):
        print(f"任务 {name} 开始")
        await asyncio.sleep(delay)
        print(f"任务 {name} 完成")
        return f"结果_{name}"
    
    task1 = task_manager.submit_task("任务A", async_task, "A", 2, priority=TaskPriority.HIGH)
    task2 = task_manager.submit_task("任务B", async_task, "B", 1, priority=TaskPriority.NORMAL)
    task3 = task_manager.submit_task("任务C", async_task, "C", 3, priority=TaskPriority.LOW)
    
    # 等待执行
    await asyncio.sleep(5)
    
    # 检查状态
    print("\n任务状态:")
    for tid in [task1, task2, task3]:
        print(task_manager.get_task_status(tid))
    
    print("\n统计信息:")
    print(task_manager.get_statistics())
    
    await task_manager.stop()

if __name__ == "__main__":
    asyncio.run(main())