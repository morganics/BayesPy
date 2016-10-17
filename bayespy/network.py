import pandas as pd
from sqlalchemy import create_engine
import uuid
from bayespy.jni import *
from bayespy.data import DataFrame
import shutil
from bayespy.model import NetworkModel
import os

def create_network():
    return bayesServer.Network(str(uuid.getnode()))


def create_network_from_file(path):
    network = create_network()
    network.load(path)
    return network


STATE_DELIMITER = "$$"


def state(variable, state):
    return "{0}{1}{2}".format(variable, STATE_DELIMITER, state)


class Discrete:
    def __init__(self, variable, state):
        self.variable = variable
        self.state = state

    def tostring(self):
        return state(self.variable, self.state)

    @staticmethod
    def fromstring(text):
        return Discrete(*text.split(STATE_DELIMITER))

    def __str__(self):
        return self.tostring()


class Builder:
    @staticmethod
    def get_variable(network, variable):
        return network.getVariables().get(variable)

    @staticmethod
    def try_get_node(network, node_name):
        try:
            n = Builder.get_node(network, node_name)
            return n
        except:
            return False

    @staticmethod
    def get_node(network, node):
        return network.getNodes().get(node)

    @staticmethod
    def create_link(network, n1, n2, t=None):
        if isinstance(n1, str):
            n1_name = n1
            n1 = Builder.get_node(network, n1)

        if isinstance(n2, str):
            n2_name = n2
            n2 = Builder.get_node(network, n2)

        if n1 is None:
            raise ValueError("N1 {} was not recognised".format(n1_name))

        if n2 is None:
            raise ValueError("N2 {} was not recognised".format(n2_name))

        if t is not None:
            l = bayesServer.Link(n1, n2, t)
        else:
            l = bayesServer.Link(n1, n2)

        try:
            network.getLinks().add(l)
        except BaseException as e:
            raise ValueError(e.message() + ". Trying to add link from {} to {}".format(n1.getName(), n2.getName()))

    @staticmethod
    def _create_interval_name(interval, decimal_places):
        title = ""
        title += "(" if interval.getMinimumEndPoint() == bayesServer.IntervalEndPoint.OPEN else "["
        title += "{0:.{digits}f},{1:.{digits}f}".format(interval.getMinimum().floatValue(), interval.getMaximum().floatValue(), digits=decimal_places)
        title += ")" if interval.getMaximumEndPoint() == bayesServer.IntervalEndPoint.OPEN else "]"
        return title

    @staticmethod
    def create_discretised_variable(network, data, node_name, bin_count=4, infinite_extremes=True, decimal_places=4, mode='EqualFrequencies'):
        options = bayesServerDiscovery.DiscretizationOptions()
        options.setInfiniteExtremes(infinite_extremes)
        options.setSuggestedBinCount(bin_count)
        values = jp.java.util.Arrays.asList(data[node_name].astype(float).dropna().tolist())
        if mode == 'EqualFrequencies':
            ef = bayesServerDiscovery.EqualFrequencies()
        elif mode == 'EqualIntervals':
            ef = bayesServerDiscovery.EqualIntervals()
        else:
            raise ValueError("mode not recognised")

        intervals = ef.discretize(values, options, jp.JString(node_name))

        v = bayesServer.Variable(node_name, bayesServer.VariableValueType.DISCRETE)
        v.setStateValueType(bayesServer.StateValueType.DOUBLE_INTERVAL)
        n = bayesServer.Node(v)
        for interval in intervals:
            v.getStates().add(bayesServer.State("{}".format(Builder._create_interval_name(interval, decimal_places)), interval))

        network.getNodes().add(n)
        return n

    @staticmethod
    def create_continuous_variable(network, node_name):
        n = Builder.try_get_node(network, node_name)
        if n is not None:
            return n
        
        v = bayesServer.Variable(node_name, bayesServer.VariableValueType.CONTINUOUS)
        n_ = bayesServer.Node(v)

        network.getNodes().add(n_)
        
        return n_

    @staticmethod
    def create_cluster_variable(network, num_states):
        n = Builder.get_node(network, "Cluster")        
        if n is not None:
            return n
        
        v = bayesServer.Variable("Cluster")        
        parent = bayesServer.Node(v)
        for i in range(num_states):
            v.getStates().add(bayesServer.State("Cluster{}".format(i)))

        network.getNodes().add(parent)
        return parent

    @staticmethod
    def create_discrete_variable(network, data, node_name, states):
        n = Builder.try_get_node(network, node_name)
        if n is not None:
            return n
            
        v = bayesServer.Variable(node_name)
        n_ = bayesServer.Node(v)

        for s in states:
            v.getStates().add(bayesServer.State(str(s)))

        if node_name in data.columns.tolist():

            if DataFrame.is_int(data[node_name].dtype) or DataFrame.could_be_int(data[node_name]):
                v.setStateValueType(bayesServer.StateValueType.INTEGER)
                for state in v.getStates():
                    state.setValue(jp.java.lang.Integer(int(float(state.getName()))))

            if DataFrame.is_bool(data[node_name].dtype):
                v.setStateValueType(bayesServer.StateValueType.BOOLEAN)
                for state in v.getStates():
                    state.setValue(state.getName() == 'True')

        network.getNodes().add(n_)

        return n_

class NetworkBuilder:
    def __init__(self, jnetwork):
        self._jnetwork = jnetwork

    def create_naive_network(self, discrete=pd.DataFrame(), continuous=pd.DataFrame(), latent_states=None,
                             parent_node=None):
        if latent_states is None and parent_node is None:
            raise ValueError("Latent_states or parent_node is a required argument")

        self._add_nodes(discrete=discrete, continuous=continuous)
        parent = self._create_parent(latent_states=latent_states, parent_node=parent_node)
        self._create_links(parent)

    def build_naive_network_with_latent_parents(self, discrete=pd.DataFrame(), continuous=pd.DataFrame(),
                                                latent_states=None):
        # self._add_nodes(discrete=discrete, continuous=continuous)
        parent = self._create_parent(latent_states=latent_states)
        if not continuous.empty:
            for c_name in continuous.columns:
                c = self._create_continuous_variable(c_name)
                node_name = "Cluster_" + c_name
                n_ = self._create_discrete_variable(discrete, node_name, ["Cluster{}".format(i) for i in range(3)])
                self._create_link(n_, c)
                self._create_link(parent, n_)

        if not discrete.empty:
            for d_name in discrete.columns:
                d = self._create_discrete_variable(discrete, d_name, discrete[d_name].dropna().unique())
                self._create_link(parent, d)

    def export(self, path):
        save(path)

    def _create_link(self, n1, n2, t=None):
        if isinstance(n1, str):
            n1 = self._get_variable(n1)

        if isinstance(n1, str):
            n2 = self._get_variable(n2)

        if t is not None:
            l = bayesServer.Link(n1, n2, t)
        else:
            l = bayesServer.Link(n1, n2)

        self._jnetwork.getLinks().add(l)

    def _create_multivariate_discrete_node(self, variables, node_name, is_temporal=False):
        vlist = []
        for v in variables:
            vlist.append(bayesServer.Variable(v, bayesServer.VariableValueType.DISCRETE))

        n_ = bayesServer.Node(node_name, vlist)
        if is_temporal:
            n_.setTemporalType(bayesServer.TemporalType.TEMPORAL)

        self._jnetwork.getNodes().add(n_)

        return n_

    def _create_multivariate_continuous_node(self, variables, node_name, is_temporal=False):
        vlist = []
        for v in variables:
            vlist.append(bayesServer.Variable(v, bayesServer.VariableValueType.CONTINUOUS))

        n_ = bayesServer.Node(node_name, vlist)
        if is_temporal:
            n_.setTemporalType(bayesServer.TemporalType.TEMPORAL)

        self._jnetwork.getNodes().add(n_)

        return n_

    def _create_continuous_variable(self, node_name):
        v = bayesServer.Variable(node_name, bayesServer.VariableValueType.CONTINUOUS)
        n_ = bayesServer.Node(v)
        self._jnetwork.getNodes().add(n_)

        return n_

    def _create_discrete_variable(self, data, node_name, states):
        v = bayesServer.Variable(node_name)
        n_ = bayesServer.Node(v)

        for s in states:
            v.getStates().add(bayesServer.State(str(s)))

        if node_name in data.columns.tolist():
            if DataFrame.is_int(data[node_name].dtype):
                v.setStateValueType(bayesServer.StateValueType.INTEGER)
                for state in v.getStates():
                    state.setValue(jp.java.lang.Integer(state.getName()))

            if DataFrame.is_bool(data[node_name].dtype):
                v.setStateValueType(bayesServer.StateValueType.BOOLEAN)
                for state in v.getStates():
                    state.setValue(state.getName() == 'True')

        self._jnetwork.getNodes().add(n_)

        return n_

    def _get_variable(self, n):
        return self._jnetwork.getVariables().get(n)

    def _create_parent(self, latent_states=None, parent_node=None):
        if latent_states is not None:
            v = bayesServer.Variable("Cluster")
            parent = bayesServer.Node(v)
            for i in range(latent_states):
                v.getStates().add(bayesServer.State("Cluster{}".format(i)))

            self._jnetwork.getNodes().add(parent)
        else:
            parent = self._jnetwork.getNodes().get(parent_node)

        return parent

    def _add_nodes(self, discrete=pd.DataFrame(), continuous=pd.DataFrame()):
        if not discrete.empty:
            for n in discrete.columns:
                v = bayesServer.Variable(n)
                n_ = bayesServer.Node(v)
                if DataFrame.is_int(discrete[n].dtype):
                    v.setStateValueType(bayesServer.StateValueType.INTEGER)

                if DataFrame.is_bool(discrete[n].dtype):
                    v.setStateValueType(bayesServer.StateValueType.BOOLEAN)

                for s in discrete[n].unique():
                    state = bayesServer.State(str(s))
                    if DataFrame.is_int(discrete[n].dtype):
                        state.setValue(jp.java.lang.Integer(state.getName()))
                    if DataFrame.is_bool(discrete[n].dtype):
                        state.setValue(bool(s))

                    v.getStates().add(state)

                self._jnetwork.getNodes().add(n_)

        if not continuous.empty:
            for n in continuous.columns:
                v = bayesServer.Variable(n, bayesServer.VariableValueType.CONTINUOUS)
                n_ = bayesServer.Node(v)
                self._jnetwork.getNodes().add(n_)

    def remove_continuous_nodes(self):
        to_remove = []
        for v in self._jnetwork.getVariables():
            if is_variable_continuous(v):
                to_remove.append(v)

        for v in to_remove:
            node = v.getNode()
            self._jnetwork.getNodes().remove(node)

    def _create_links(self, parent):
        for node in self._jnetwork.getNodes():
            if node == parent:
                continue

            l = bayesServer.Link(parent, node)
            self._jnetwork.getLinks().add(l)

def is_variable_discrete(v):
    return v.getValueType() == bayesServer.VariableValueType.DISCRETE

def is_variable_continuous(v):
    return v.getValueType() == bayesServer.VariableValueType.CONTINUOUS

def get_variable(network, variable_name):
    variable = network.getVariables().get(variable_name)
    if variable is None:
        raise ValueError("Variable {} does not exist".format(variable_name))

    return variable

def variable_exists(network, variable_name):
    try:
        get_variable(network, variable_name)
        return True
    except ValueError:
        return False

def remove_continuous_nodes(network):
    n = network.copy()
    to_remove = []
    for v in n.getVariables():
        if is_variable_continuous(v):
            to_remove.append(v)

    for v in to_remove:
        node = v.getNode()
        n.getNodes().remove(node)

    return n

def get_number_of_states(network, variable):
    v = network.getVariables().get(variable)
    return len(v.getStates())

def get_state(network, variable_name, state_name):
    variable = get_variable(network, variable_name)
    for jstate in variable.getStates():
        if jstate.getName() == str(state_name):
            return jstate

def get_other_states_from_variable(network, target):
    target_ = network.getVariables().get(target.variable)
    for st in target_.getStates():
        if st.getName() == str(target.state):
            continue

        yield state(target.variable, st.getName())


def create_variable_references(network, data):
    """
    Match up network variables to the dataframe columns
    :param data: dataframe
    :return: a list of 'VariableReference' objects
    """
    latent_variable_name = "Cluster"
    for v in network.getVariables():
        if v.getName().startswith(latent_variable_name):
            continue

        valueType = bayesServer.data.ColumnValueType.VALUE

        if v.getStateValueType() != bayesServer.StateValueType.DOUBLE_INTERVAL \
                and bayespy.network.is_variable_discrete(v):

            if not DataFrame.is_int(data[v.getName()].dtype) and not DataFrame.is_bool(data[v.getName()].dtype):
                valueType = bayesServer.data.ColumnValueType.NAME

        yield bayesServer.data.VariableReference(v, valueType, v.getName())

def save(network, path):
    from xml.dom import minidom
    nt = network.saveToString()
    reparsed = minidom.parseString(nt)
    with open(path, 'w') as fh:
        fh.write(reparsed.toprettyxml(indent="  "))


def is_cluster_variable(v):
    if not isinstance(v, str):
        v = v.getName()
    return v == "Cluster" or v.startswith("Cluster_")

def is_trained(network):
    for n in network.getNodes():
        if n.getDistribution() is None:
            return False
            
    return True


class DataStore:
    def __init__(self, logger, db_folder, dataframe):
        self.uuid = str(uuid.uuid4()).replace("-","")
        self._db_dir = os.path.join(db_folder, "db")
        self._create_folder()
        filename = "sqlite:///{}.db".format(os.path.join(self._db_dir, self.uuid))
        self._engine = create_engine(filename)
        self.table = "table_" + self.uuid
        self._logger = logger
        self.data = dataframe

    def get_dataframe(self):
        return self.data

    def get_connection(self):
        return "jdbc:sqlite:{}.db".format(os.path.join(self._db_dir, self.uuid))

    def _create_folder(self):
        if not os.path.exists(self._db_dir):
            os.makedirs(self._db_dir)

    def write(self):
        self._logger.info("Writing {} rows to storage".format(len(self.data)))
        self.data.to_sql("table_" + self.uuid, self._engine, if_exists='replace', index_label='ix', index=True)
        self._logger.info("Finished writing {} rows to storage".format(len(self.data)))

    def create_data_reader_command(self, indexes=[]):
        """
        Get the data reader
        :param indexes: training/ testing indexes
        :return: a a DatabaseDataReaderCommand
        """

        if len(indexes) == 0:
            indexes = self.get_dataframe().index.tolist()

        data_reader_command = bayesServer.data.DatabaseDataReaderCommand(
            self.get_connection(),
            "select * from {} where ix in ({})".format(self.table, ",".join(str(i) for i in indexes)))

        return data_reader_command

    def cleanup(self):
        self._logger.debug("Cleaning up: deleting db folder")
        try:
            shutil.rmtree(self._db_dir)
        except:
            self._logger.error("Could not delete the db folder {} for some reason.".format(self._db_dir))

class NetworkFactory:
    def __init__(self, data, db_folder, logger, network_file_path = None) -> object:
        self._logger = logger
        self._data = data
        self._network_file_path = network_file_path
        self._db_folder = db_folder

    def reset_dataframe(self, df):
        self._data = df

    def _write_data(self):
        ds = DataStore(self._logger, self._db_folder, self._data)
        ds.write()
        self._datastore = ds

    def get_datastore(self):
        return self._datastore

    def get_data(self):
        return self._data

    def create_from_file(self, path):
        return create_network_from_file(path)

    def create(self):
        if self._network_file_path is None or not os.path.exists(self._network_file_path):
            return create_network()
        else:
            return self.create_from_file(self._network_file_path)

    def create_network(self) -> (object, NetworkBuilder):
        network = create_network()
        nb = NetworkBuilder(network)
        return (network, nb)

    def create_network_builder(self, network):
        return NetworkBuilder(network)

    def create_trained_model(self, network, train_indexes):
        pl = NetworkModel(self._data, network, self._datastore, self._logger)
        pl.train(train_indexes)
        return pl

    def cleanup(self):
        self._datastore.cleanup()

    def __enter__(self):
        self._write_data()
        return self

    def __exit__(self, type, value, traceback):
        self.cleanup()
        #pass
