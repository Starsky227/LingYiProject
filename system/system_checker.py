# system_checker.py - 系统检查模块
"""
提供系统环境和依赖检查功能，如果环境不正确自主执行setup.py进行修复。
"""

import sys
from pathlib import Path

# 确保项目根目录在sys.path中，以便直接运行时也能正确导入
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from system.config import config

# 全局变量：Neo4j是否可用，供其他模块快速判断
_neo4j_available: bool = False
_neo4j_checked: bool = False

def is_neo4j_available() -> bool:
    """获取Neo4j是否可用的状态（函数形式，首次调用时自动执行检测）"""
    global _neo4j_checked
    if not _neo4j_checked:
        checker = SystemChecker()
        checker.check_neo4j_connection()
        _neo4j_checked = True
    return _neo4j_available

class SystemChecker:
    """系统环境检测器"""
    
    def __init__(self):
        # 需要检测的端口 - 从config读取
        from system.config import get_all_server_ports
        all_ports = get_all_server_ports()
        self.required_ports = [
            all_ports["api_server"],
            all_ports["agent_server"], 
            all_ports["mcp_server"],
        ]

    def check_neo4j_connection(self) -> bool:
        """检测Neo4j连接"""
        global _neo4j_available
        print("正在检测Neo4j连接...")
        try:
            # 检查配置文件是否存在
            if not config.grag.enabled:
                print(f"   ⚠️ Neo4j未启用（配置中grag.enabled=false）")
                _neo4j_available = False
                return True

            uri = config.grag.neo4j_uri
            user = config.grag.neo4j_user
            password = config.grag.neo4j_password
            database = config.grag.neo4j_database

            # 尝试导入neo4j包
            try:
                from neo4j import GraphDatabase
            except ImportError:
                print(f"   ❌ Neo4j包未安装，请运行: pip install neo4j")
                _neo4j_available = False
                return False

            # 实际测试连接
            print(f"   Neo4j配置: {uri} (用户: {user}, 数据库: {database})")
            driver = None
            try:
                driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=5)
                driver.verify_connectivity()
                print(f"   ✅ Neo4j连接成功")
                _neo4j_available = True
                return True
            except Exception as e:
                print(f"   ❌ Neo4j连接失败: {e}")
                print(f"   💡 请确保Neo4j服务正在运行，且用户名/密码正确")
                _neo4j_available = False
                return False
            finally:
                if driver:
                    driver.close()

        except Exception as e:
            print(f"   ❌ Neo4j检测异常: {e}")
            _neo4j_available = False
            return False

    def run_all_checks(self) -> dict:
        """运行所有系统检测项，返回检测结果汇总"""
        print("🔍 正在进行系统环境检测...")
        print("=" * 40)
        results = {}

        # 1. Neo4j连接检测
        results['neo4j'] = self.check_neo4j_connection()

        # 未来可在此添加更多检测项，例如：
        # results['redis'] = self.check_redis_connection()
        # results['dependencies'] = self.check_dependencies()
        # results['ports'] = self.check_ports()

        # 汇总结果
        print("=" * 40)
        passed = sum(1 for v in results.values() if v)
        total = len(results)
        print(f"📊 系统检测完成: {passed}/{total} 项通过")
        print("=" * 40)

        self.results = results
        return results


def run_system_check() -> dict:
    """模块级便捷函数：执行完整系统检测"""
    checker = SystemChecker()
    return checker.run_all_checks()


if __name__ == "__main__":
    run_system_check()